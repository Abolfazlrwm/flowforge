"""Persistence models for the execution domain.

These are the *durable* half of the domain. The rules live in
:mod:`core.domain`; this module mirrors them as CHECK constraints so that a bug,
a concurrent writer or a manual ``UPDATE`` cannot leave a row in a state the
domain considers impossible.

Every index here exists to serve a named query. If you cannot name the query,
do not add the index — see ``docs/architecture/data-model.md`` for the list and
the plan behind each one.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.domain.states import TaskRunState, WorkflowRunState

WORKFLOW_RUN_TERMINAL = tuple(
    state.value
    for state in (WorkflowRunState.SUCCEEDED, WorkflowRunState.FAILED, WorkflowRunState.CANCELLED)
)
TASK_RUN_TERMINAL = tuple(
    state.value for state in (TaskRunState.SUCCEEDED, TaskRunState.DEAD, TaskRunState.CANCELLED)
)

WORKFLOW_RUN_STATE_CHOICES = [(state.value, state.name.title()) for state in WorkflowRunState]
TASK_RUN_STATE_CHOICES = [(state.value, state.name.title()) for state in TaskRunState]


class Workflow(models.Model):
    """A versioned workflow definition.

    ``(name, version)`` is unique: publishing a new definition creates a new
    row rather than mutating the old one, so a run can always be traced back to
    the definition it actually executed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    version = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table-level rules."""

        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"],
                name="uniq_workflow_name_version",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="workflow_version_positive",
            ),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"{self.name} v{self.version}"


class Task(models.Model):
    """One step in a workflow definition.

    ``key`` is unique per workflow so that a run can refer to a step by name and
    resolve it deterministically, which is what the Phase 2 DAG needs.

    ``task_type``, ``depends_on`` and ``config`` live here rather than in a JSON
    blob on ``Workflow`` so that the rows are a faithful, queryable record of the
    definition. Loading a definition reconstructs exactly what was published,
    with no second source of truth to drift.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="tasks")
    key = models.CharField(max_length=200)
    task_type = models.CharField(max_length=200)
    depends_on = models.JSONField(default=list)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table-level rules."""

        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "key"],
                name="uniq_task_workflow_key",
            ),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"{self.workflow.name}.{self.key}"


class WorkflowRun(models.Model):
    """One execution of a :class:`Workflow`.

    The terminal/non-terminal split is enforced in the database rather than
    trusted to callers: a row in a terminal state without ``finished_at`` would
    make every duration metric wrong, and a row with ``finished_at`` that is
    still running would make the run look finished to anything sorting by it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(Workflow, on_delete=models.PROTECT, related_name="runs")
    state = models.CharField(
        max_length=20,
        choices=WORKFLOW_RUN_STATE_CHOICES,
        default=WorkflowRunState.PENDING.value,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # Who started this run. Nullable because a run can be started by the
    # scheduler itself (a scheduled or retried trigger) with no request behind
    # it. SET_NULL rather than CASCADE: deleting a user must not delete the
    # history of what their workflows did.
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workflow_runs",
    )

    class Meta:
        """Table-level rules and the indexes behind named queries."""

        constraints = [
            models.CheckConstraint(
                condition=Q(state__in=[s.value for s in WorkflowRunState]),
                name="wfrun_state_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(state__in=WORKFLOW_RUN_TERMINAL, finished_at__isnull=False)
                    | (~Q(state__in=WORKFLOW_RUN_TERMINAL) & Q(finished_at__isnull=True))
                ),
                name="wfrun_finished_at_matches_state",
            ),
        ]
        indexes = [
            # "Show me the last N runs of this workflow."
            models.Index(fields=["workflow", "-created_at"], name="idx_wfrun_wf_recent"),
            # "Which runs have been in this state longer than X?" (stuck-run sweep).
            models.Index(fields=["state", "created_at"], name="idx_wfrun_state_age"),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"run {self.id} of {self.workflow_id} ({self.state})"


class TaskRun(models.Model):
    """One attempt at a :class:`Task` within a :class:`WorkflowRun`.

    Retries reuse this row and increment ``attempt`` rather than inserting a new
    one, so ``(workflow_run, task)`` is unique and "what happened to task X in
    run Y" is a single-row read.

    ``fencing_token`` is a monotonic integer incremented on every claim. It is
    the mechanism that makes a resurrected worker harmless: the worker that
    held token 3 cannot write a result after another worker has claimed token 4,
    because its conditional ``UPDATE`` matches no row.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow_run = models.ForeignKey(
        WorkflowRun, on_delete=models.CASCADE, related_name="task_runs"
    )
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="task_runs")
    state = models.CharField(
        max_length=20,
        choices=TASK_RUN_STATE_CHOICES,
        default=TaskRunState.PENDING.value,
    )
    attempt = models.PositiveIntegerField(default=1)
    fencing_token = models.PositiveBigIntegerField(default=0)
    lease_owner = models.CharField(max_length=200, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # When a task is RETRYING, this is the earliest moment it may be promoted back
    # to READY. It encodes the backoff delay so the scheduler needs no clock math.
    scheduled_at = models.DateTimeField(null=True, blank=True)
    # The durable return value of the handler, persisted on success. Retries reuse
    # the row, so the latest attempt's result overwrites the previous; a failed
    # attempt leaves it null. Phase 6 adds this.
    result = models.JSONField(null=True, blank=True)
    # An optional key that makes a handler idempotent: two runs with the same key
    # (e.g. from two triggers of the same logical operation) execute once, and the
    # second reuses the first's result. Null means "not idempotent".
    idempotency_key = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Table-level rules and the indexes behind named queries."""

        constraints = [
            models.UniqueConstraint(
                fields=["workflow_run", "task"],
                name="uniq_taskrun_per_run_task",
            ),
            models.CheckConstraint(
                condition=Q(state__in=[s.value for s in TaskRunState]),
                name="taskrun_state_valid",
            ),
            models.CheckConstraint(
                condition=Q(attempt__gte=1),
                name="taskrun_attempt_positive",
            ),
            # A backoff delay exists only while a task is RETRYING; in every other
            # state it must be null, so a row cannot "wake up" from READY/PENDING
            # or carry a stale delay into SUCCEEDED/DEAD.
            models.CheckConstraint(
                condition=(
                    Q(state=TaskRunState.RETRYING.value, scheduled_at__isnull=False)
                    | (~Q(state=TaskRunState.RETRYING.value) & Q(scheduled_at__isnull=True))
                ),
                name="taskrun_scheduled_iff_retrying",
            ),
            models.CheckConstraint(
                condition=Q(fencing_token__gte=0),
                name="taskrun_fencing_non_negative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(finished_at__isnull=True)
                    | Q(started_at__isnull=True)
                    | Q(finished_at__gte=F("started_at"))
                ),
                name="taskrun_finished_after_started",
            ),
            # A lease exists exactly while the task is running. Enforced as an
            # equivalence so neither half can drift: no running row without a
            # lease, no idle row holding one.
            models.CheckConstraint(
                condition=(
                    Q(state=TaskRunState.RUNNING.value, lease_expires_at__isnull=False)
                    | (~Q(state=TaskRunState.RUNNING.value) & Q(lease_expires_at__isnull=True))
                ),
                name="taskrun_lease_iff_running",
            ),
        ]
        indexes = [
            # The scheduler's claim query: oldest READY task first.
            models.Index(fields=["state", "created_at"], name="idx_taskrun_state_age"),
            # Run progress: "how many of this run's tasks are in state X?"
            models.Index(fields=["workflow_run", "state"], name="idx_taskrun_run_state"),
            # The lease reaper: only ever scans running rows, so a partial index
            # keeps it proportional to in-flight work rather than table size.
            models.Index(
                fields=["lease_expires_at"],
                name="idx_taskrun_lease_expiry",
                condition=Q(state=TaskRunState.RUNNING.value),
            ),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"task {self.task_id} in run {self.workflow_run_id} ({self.state})"


class IdempotencyKey(models.Model):
    """A claim on an ``Idempotency-Key`` header, scoped to one user.

    The uniqueness constraint here is the whole mechanism behind "N parallel
    requests with the same key create exactly one run". Each request tries to
    insert its claim; the unique index on ``(user, key)`` lets exactly one win,
    and the losers block until the winner commits, then fail with an
    ``IntegrityError`` and read back what the winner produced.

    A separate table rather than a column on ``WorkflowRun``, for two reasons:

    * **It scopes to the user, not the run.** Two users may legitimately choose
      the same key, and neither should be able to guess or block the other's.
    * **It records the response.** Replaying a request must return the original
      status and body, not a freshly computed one that may differ because state
      moved on.

    ``request_fingerprint`` is what distinguishes a replay from a *misuse*: the
    same key with a different payload is a client bug, and returning the original
    response for it would silently discard the second request.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="idempotency_keys",
    )
    key = models.CharField(max_length=255)
    request_fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    workflow_run = models.ForeignKey(
        WorkflowRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="idempotency_keys",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table-level rules."""

        constraints = [
            # The race is resolved by this constraint, not by a lock: a row that
            # does not exist yet cannot be locked, and PostgreSQL has no gap locks
            # to make `select_for_update()` help.
            models.UniqueConstraint(fields=["user", "key"], name="uniq_idempotency_user_key"),
        ]
        indexes = [
            # Replay lookup: the only query this table serves.
            models.Index(fields=["user", "key"], name="idx_idempotency_lookup"),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"idempotency {self.key!r} for user {self.user_id}"


class OutboxMessage(models.Model):
    """A dispatch event awaiting publication to the broker.

    The outbox is the remedy for the dual-write problem: a task run becomes
    dispatchable (``READY``) and an outbox row is written in *one* transaction, so
    a crash between the two can never lose the message. The publisher later reads
    these rows and publishes them to RabbitMQ with publisher confirms; only after a
    confirmed publish is the row marked delivered.

    Each row is one **dispatch generation**. A task run is dispatched once per
    generation, identified by ``(task_run, fencing_token_snapshot)``: the scheduler
    bumps the fencing token on every claim and on every reaper pass, so a re-dispatch
    after a reaped lease produces a genuinely new generation and a genuinely new row,
    while two scheduler passes over the same live generation collide on the unique
    constraint and exactly one wins.

    ``id`` is the dispatch identity the worker uses to de-duplicate redeliveries: if
    the publisher crashes after sending but before marking the row, the next sweep
    re-publishes the *same* id, and the consumer recognises it instead of executing
    twice. The payload therefore carries the id by value.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_run = models.ForeignKey(TaskRun, on_delete=models.PROTECT, related_name="outbox_messages")
    workflow_run = models.ForeignKey(
        WorkflowRun, on_delete=models.PROTECT, related_name="outbox_messages"
    )
    # The AMQP exchange and routing key the publisher should use. Constant for now;
    # Phase 5 may route by ``task_type`` without changing this table.
    exchange = models.CharField(max_length=200, default="flowforge.direct")
    routing_key = models.CharField(max_length=200, default="task.dispatch")
    # The message body. Carries the dispatch id, the task identity and a snapshot of
    # the task's configuration at dispatch time, so the worker needs no other lookup.
    payload = models.JSONField(default=dict)
    # The generation this row represents, frozen at creation. Part of the unique key.
    fencing_token_snapshot = models.PositiveBigIntegerField(default=0)
    attempt_snapshot = models.PositiveIntegerField(default=1)
    # Null until the publisher has confirmed delivery. This column is the whole
    # recoverability story: a null value means "publish me", a timestamp means "done".
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table-level rules and the indexes the publisher and scheduler rely on."""

        constraints = [
            # Exactly one outbox row per dispatch generation. Two schedulers racing
            # over the same live generation collides here; the loser catches the
            # IntegrityError and skips, so no duplicate message is ever published.
            models.UniqueConstraint(
                fields=["task_run", "fencing_token_snapshot"],
                name="uniq_outbox_per_taskrun_generation",
            ),
        ]
        indexes = [
            # The publisher's pending scan: every null ``published_at`` in creation
            # order. Partial so it tracks only undelivered work, not the whole table.
            models.Index(
                fields=["created_at"],
                name="idx_outbox_pending",
                condition=Q(published_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"outbox {self.id} for task run {self.task_run_id}"
