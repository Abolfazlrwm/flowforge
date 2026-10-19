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

from django.db import models
from django.db.models import F, Q

from core.domain.states import TaskRunState, WorkflowRunState

WORKFLOW_RUN_TERMINAL = tuple(
    state.value
    for state in (WorkflowRunState.SUCCEEDED, WorkflowRunState.FAILED, WorkflowRunState.CANCELLED)
)
TASK_RUN_TERMINAL = tuple(state.value for state in (TaskRunState.SUCCEEDED, TaskRunState.CANCELLED))

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
