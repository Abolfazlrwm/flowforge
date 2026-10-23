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
from datetime import datetime
from typing import TypeVar

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.domain.states import TaskRunState, WorkflowRunState

#: The model a scoped manager is bound to. A ``TypeVar`` keeps ``objects.create``
#: and ``objects.filter`` typed as the concrete model under mypy strict, which a
#: bare ``models.Manager`` would erase to ``Any``.
_M = TypeVar("_M", bound=models.Model)

WORKFLOW_RUN_TERMINAL = tuple(
    state.value
    for state in (WorkflowRunState.SUCCEEDED, WorkflowRunState.FAILED, WorkflowRunState.CANCELLED)
)
TASK_RUN_TERMINAL = tuple(
    state.value
    for state in (
        TaskRunState.SUCCEEDED,
        TaskRunState.SKIPPED,
        TaskRunState.DEAD,
        TaskRunState.CANCELLED,
    )
)

WORKFLOW_RUN_STATE_CHOICES = [(state.value, state.name.title()) for state in WorkflowRunState]
TASK_RUN_STATE_CHOICES = [(state.value, state.name.title()) for state in TaskRunState]


class OrganizationScopedManager(models.Manager[_M]):
    """A manager that refuses to hand back rows outside one organization.

    ``for_organization(org)`` is the sanctioned read path for a tenant-scoped
    model: it filters on the ``organization`` column (or, for a model that reaches
    its tenant through a parent, the equivalent join) so a caller cannot read
    across tenants by accident. Cross-tenant access is meant to be impossible by
    construction — every endpoint and service goes through here — and the
    tenant-isolation suite asserts it on every endpoint.
    """

    def for_organization(self, organization: Organization | None) -> models.QuerySet[_M]:
        """Rows belonging to ``organization`` only.

        ``None`` matches the pre-backfill rows whose tenant is not yet known; it
        is used by the unit fixtures and the migration path, never by an API
        caller, which always resolves a real organization.
        """
        return self.filter(organization=organization)


class Organization(models.Model):
    """A tenant. Every definition, run, task run and trigger belongs to one.

    This is the shared-schema multi-tenancy boundary (ADR-014): one database, an
    ``organization`` foreign key on every tenant row, and access always scoped by
    organization. There is no per-tenant schema or database.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Table-level rules."""

        ordering = ["name"]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return self.name


class Membership(models.Model):
    """A user's role within one organization.

    Roles are ordered ``owner > admin > member > viewer``. A user may belong to
    several organizations with a different role in each; there is no global role,
    which is what keeps one tenant's admin from being another tenant's admin.
    """

    class Role(models.TextChoices):
        """The role a member holds, most privileged first."""

        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """One role per (user, organization)."""

        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="uniq_membership_org_user",
            ),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"{self.user} in {self.organization} ({self.role})"


class ApiKey(models.Model):
    """A hashed, organization-scoped API credential.

    The plaintext key is shown exactly once, at creation. Only a hash is stored,
    so a database dump cannot be replayed. A short random ``prefix`` is stored in
    the clear because it is how a presented key is looked up without hashing every
    row: the full key is ``ff_<prefix>_<secret>``, and only ``secret`` is hashed.

    Keys are revoked rather than deleted (``revoked_at``) so an audit trail of
    "this credential existed and was withdrawn" survives. They may also expire.
    """

    class Scope(models.TextChoices):
        """What the key may do. ``admin`` implies ``write`` implies ``read``."""

        READ = "read", "Read"
        WRITE = "write", "Write"
        ADMIN = "admin", "Admin"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=200)
    #: The non-secret lookup handle, unique across every tenant.
    prefix = models.CharField(max_length=32, unique=True)
    #: ``make_password(secret)`` — never the secret itself.
    hashed_key = models.CharField(max_length=256)
    #: The scopes granted to this key, as a list of ``Scope`` values.
    scopes = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="api_keys",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = OrganizationScopedManager()

    class Meta:
        """Tenant-scoped listing index."""

        indexes = [
            models.Index(fields=["organization", "created_at"], name="idx_apikey_org_created"),
        ]

    def is_usable(self, *, now: datetime | None = None) -> bool:
        """Whether the key may authenticate a request right now."""
        moment = now if now is not None else timezone.now()
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        return self.expires_at > moment

    def has_scope(self, required: str) -> bool:
        """Whether this key grants at least ``required``, honouring hierarchy."""
        order = {"read": 0, "write": 1, "admin": 2}
        threshold = order.get(required, 99)
        return any(order.get(scope, -1) >= threshold for scope in self.scopes)

    def __str__(self) -> str:
        """Readable label; never includes the secret."""
        return f"api key {self.prefix} ({self.name})"


class Workflow(models.Model):
    """A versioned workflow definition.

    ``(name, version)`` is unique: publishing a new definition creates a new
    row rather than mutating the old one, so a run can always be traced back to
    the definition it actually executed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Nullable only so the backfill migration is reversible; publish_definition
    # always sets it, so no live row is ever unscoped (see ADR-014).
    organization = models.ForeignKey(
        Organization, null=True, on_delete=models.PROTECT, related_name="workflows"
    )
    name = models.CharField(max_length=200)
    version = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = OrganizationScopedManager()

    class Meta:
        """Table-level rules."""

        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name", "version"],
                name="uniq_workflow_name_version",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="workflow_version_positive",
            ),
        ]
        indexes = [
            # "List this organization's definitions." Every scoped read filters here.
            models.Index(fields=["organization", "name"], name="idx_workflow_org_name"),
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
    #: Optional condition, in the sandboxed expression language. Stored as text so
    #: the row is a faithful record of what was published; it is re-parsed and
    #: validated on load, which is what keeps a hand-edited row from reaching the
    #: scheduler as an executable string.
    when = models.CharField(max_length=2000, null=True, blank=True)
    #: "all" (every dependency must succeed) or "any" (one is enough).
    join = models.CharField(max_length=8, default="all")
    #: Optional key of the forward task this one compensates. A compensation task
    #: is excluded from the forward DAG and runs, in reverse dependency order,
    #: only when the run fails (saga rollback).
    compensate_for = models.CharField(max_length=200, null=True, blank=True)
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
    # Nullable only so the backfill migration is reversible; every run is created
    # with an organization by the trigger path, so no live run is unscoped.
    organization = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.PROTECT, related_name="runs"
    )
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

    objects = OrganizationScopedManager()

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
            # "List this organization's runs" — the tenant-scoped run list.
            models.Index(fields=["organization", "-created_at"], name="idx_wfrun_org_recent"),
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
    # Tenant scoping: a task run always belongs to the same organization as its
    # run. Nullable only for the reversible backfill; the trigger/claim paths
    # always set it.
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="task_runs",
    )
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

    objects = OrganizationScopedManager()

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
            # Tenant scoping: "every task run of this organization", by construction.
            models.Index(fields=["organization"], name="idx_taskrun_org"),
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
    # Tenant scoping: an idempotency key is scoped to the organization that owns
    # the run it guards, so a replay in one tenant can never return another
    # tenant's run. Nullable only for the reversible backfill.
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="idempotency_keys",
    )
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

    objects = OrganizationScopedManager()

    class Meta:
        """Table-level rules."""

        constraints = [
            # The race is resolved by this constraint, not by a lock: a row that
            # does not exist yet cannot be locked, and PostgreSQL has no gap locks
            # to make `select_for_update()` help. Scoped per organization so two
            # tenants may independently reuse a key without colliding.
            models.UniqueConstraint(
                fields=["organization", "user", "key"], name="uniq_idempotency_org_user_key"
            ),
        ]
        indexes = [
            # Replay lookup: the only query this table serves.
            models.Index(fields=["organization", "user", "key"], name="idx_idempotency_lookup"),
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
    # Tenant scoping: the publisher drains this table and must never cross
    # tenants, so every dispatch row carries its organization. Nullable only for
    # the reversible backfill.
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="outbox_messages",
    )
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

    objects = OrganizationScopedManager()

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
            # Tenant scoping: a per-organization pending scan, by construction.
            models.Index(fields=["organization"], name="idx_outbox_org"),
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


class ScheduledTrigger(models.Model):
    """A cron-based trigger that starts a workflow run on a schedule.

    The scheduler polls ``next_run_at``; when it is due, the trigger is claimed
    atomically with ``SELECT FOR UPDATE SKIP LOCKED`` so N scheduler instances
    cannot fire the same trigger twice. The ``next_run_at`` is advanced *before*
    the workflow is triggered, so a crash during trigger does not cause a
    double-fire on recovery — the trigger already says "I fired at this time".

    If the trigger's workflow is not published, the tick is a no-op and the
    trigger advances anyway (the operator must fix the workflow, not the clock).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Tenant scoping: a cron trigger belongs to the organization that owns its
    # workflow. Nullable only for the reversible backfill.
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="scheduled_triggers",
    )
    workflow = models.ForeignKey(
        Workflow, on_delete=models.CASCADE, related_name="scheduled_triggers"
    )
    #: Cron expression in the standard 5-field format: ``min hour dom month dow``.
    cron_expression = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    #: The next time this trigger is eligible to fire. Advanced by the scheduler.
    next_run_at = models.DateTimeField(db_index=True)
    #: The last time the trigger successfully fired. Null until the first tick.
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = OrganizationScopedManager()

    class Meta:
        """Index the poll query: enabled and due, ordered by soonest first."""

        indexes = [
            models.Index(
                fields=["enabled", "next_run_at"],
                name="idx_trigger_due",
            ),
            # Tenant scoping: "this organization's due triggers", by construction.
            models.Index(
                fields=["organization", "enabled", "next_run_at"],
                name="idx_trigger_org_due",
            ),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"trigger {self.id} for {self.workflow.name} ({self.cron_expression})"


class TaskRunEvent(models.Model):
    """An append-only record of one task-run state change.

    The event log is the audit trail of execution: every transition performed by
    the scheduler, a worker, the reaper or an operator is recorded here, in the
    order it happened. It is never updated or deleted, so a run's history stays
    reconstructable even after the run row itself is gone. ``kind`` names the
    actor; ``from_state``/``to_state`` give the precise edge. ``from_state`` is
    null only for operator cancellations, where the prior state is implied by the
    event that precedes it.
    """

    class Kind(models.TextChoices):
        """Who performed the transition."""

        CLAIM = "claim", "Claimed by a worker"
        FINISH = "finish", "Finished by a worker"
        CANCEL = "cancel", "Cancelled by an operator"
        REQUEUE = "requeue", "Requeued by an operator"
        RECLAIM = "reclaim", "Lease reclaimed by the reaper"
        PROMOTE = "promote", "Promoted to ready"
        RETRY = "retry", "Scheduled for retry"
        DEAD = "dead", "Exhausted retries"
        SKIP = "skip", "Branch skipped"
        TRANSITION = "transition", "State transition"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Tenant scoping: an event belongs to the organization of its task run.
    # Nullable only for the reversible backfill.
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="task_run_events",
    )
    task_run = models.ForeignKey(TaskRun, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    from_state = models.CharField(max_length=20, null=True, blank=True)
    to_state = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = OrganizationScopedManager()

    class Meta:
        """Order by time, and index the per-run history query."""

        ordering = ["created_at", "id"]
        indexes = [
            models.Index(
                fields=["task_run", "created_at"],
                name="idx_event_taskrun_created",
            ),
            # Tenant scoping: "this organization's event log", by construction.
            models.Index(
                fields=["organization", "created_at"],
                name="idx_event_org_created",
            ),
        ]

    def __str__(self) -> str:
        """Readable label for the admin and logs."""
        return f"event {self.id}: {self.task_run_id} {self.from_state} -> {self.to_state}"
