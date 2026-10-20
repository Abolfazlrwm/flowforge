"""Atomic state transitions.

The contract, which the tests pin down:

- An **illegal** transition (absent from the table) raises
  :class:`~core.domain.errors.IllegalTransitionError` before any SQL runs. That is a
  bug in the caller, and it should fail the same way whether or not a row exists.
- A **legal** transition that loses a race returns ``False``. That is not an
  error: another writer got there first, which is exactly what the conditional
  ``UPDATE`` is for.

Every write is a single ``UPDATE ... WHERE id = ? AND state = ?``. The database
decides the winner; no read-then-write window exists, so no lock is needed and
no sleep is needed to make the race reliable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.domain.errors import IllegalTransitionError
from core.domain.machines import TASK_RUN_MACHINE, WORKFLOW_RUN_MACHINE
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun, WorkflowRun


def _resolve_now(now: datetime | None) -> datetime:
    """Return ``now`` if given, otherwise the current time.

    Callers pass an explicit value when they need deterministic assertions; the
    default exists so production callers do not have to.
    """
    return now if now is not None else timezone.now()


def transition_workflow_run(
    *,
    run_id: UUID,
    expected: WorkflowRunState,
    target: WorkflowRunState,
    now: datetime | None = None,
) -> bool:
    """Move a workflow run from ``expected`` to ``target``, if it is still there.

    Returns:
        ``True`` if this call performed the transition, ``False`` if the row was
        no longer in ``expected``.

    Raises:
        IllegalTransitionError: if ``expected -> target`` is not a declared edge.
    """
    WORKFLOW_RUN_MACHINE.apply(expected, target)

    moment = _resolve_now(now)
    updates: dict[str, object] = {"state": target.value}
    if WORKFLOW_RUN_MACHINE.is_terminal(target):
        updates["finished_at"] = moment
    if expected is WorkflowRunState.PENDING and target is WorkflowRunState.RUNNING:
        updates["started_at"] = moment

    return WorkflowRun.objects.filter(pk=run_id, state=expected.value).update(**updates) == 1


def reclaim_task_run(
    *,
    task_run_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Return a RUNNING task run whose lease has expired back to ``READY``.

    This is the lease reaper's per-row operation. It is a conditional ``UPDATE``
    that matches only while the row is still ``RUNNING`` *and* its lease has
    already expired, so a live lease is never reclaimed and a row another reaper
    just reclaimed is never reclaimed twice.

    Reclaiming bumps the fencing token. The worker that held the expired lease may
    still be alive and may try to ``finish_task_run``; its token is now stale (the
    row is ``READY``, and even a later re-claim increments the token again), so its
    write matches zero rows. The reaper is what makes a resurrected worker
    harmless without the broker ever knowing one existed.

    Returns:
        ``True`` if this call reclaimed the row, ``False`` if the row was gone,
        no longer running, or its lease had not yet expired.
    """
    TASK_RUN_MACHINE.apply(TaskRunState.RUNNING, TaskRunState.READY)

    moment = _resolve_now(now)
    with transaction.atomic():
        reclaimed = TaskRun.objects.filter(
            pk=task_run_id,
            state=TaskRunState.RUNNING.value,
            lease_expires_at__lt=moment,
        ).update(
            state=TaskRunState.READY.value,
            lease_owner=None,
            lease_expires_at=None,
            # Invalidate any in-flight attempt by the dead worker. A later claim
            # increments again, so even a second reaper pass keeps the token
            # strictly ahead of what the dead worker holds.
            fencing_token=F("fencing_token") + 1,
            started_at=Coalesce(F("started_at"), Value(moment)),
        )
        return reclaimed == 1


def reap_expired_leases(*, now: datetime | None = None, limit: int = 1000) -> int:
    """Reclaim every ``RUNNING`` task run whose lease has expired.

    Uses the partial index ``idx_taskrun_lease_expiry`` (``state = running``), so
    the scan touches only in-flight work rather than the whole table.

    Returns:
        The number of task runs reclaimed in this pass.
    """
    moment = _resolve_now(now)
    candidates = list(
        TaskRun.objects.filter(
            state=TaskRunState.RUNNING.value,
            lease_expires_at__lt=moment,
        ).values_list("id", flat=True)[:limit]
    )
    reclaimed = 0
    for task_run_id in candidates:
        if reclaim_task_run(task_run_id=task_run_id, now=moment):
            reclaimed += 1
    return reclaimed


def transition_task_run(
    *,
    task_run_id: UUID,
    expected: TaskRunState,
    target: TaskRunState,
    now: datetime | None = None,
) -> bool:
    """Move a task run between non-lease states, if it is still in ``expected``.

    This deliberately does **not** handle the transitions that require a lease.
    Passing one raises :class:`~core.domain.errors.LeaseRequiredError`, which points
    the caller at :func:`finish_task_run` — the only path that can prove
    ownership with a fencing token.

    Returns:
        ``True`` if this call performed the transition, ``False`` otherwise.

    Raises:
        IllegalTransitionError: if ``expected -> target`` is not a declared edge.
        LeaseRequiredError: if the edge requires a held lease.
    """
    TASK_RUN_MACHINE.apply(expected, target)

    # The only non-lease edge out of RUNNING is the reaper (`reap_lease`), which
    # must clear the lease and bump the fencing token. `transition_task_run` does
    # neither, so it must never be the path for it — the caller is routed to
    # `reclaim_task_run` instead. Every other RUNNING edge requires a lease and is
    # already refused above with `LeaseRequiredError`.
    if expected is TaskRunState.RUNNING and target is TaskRunState.READY:
        raise IllegalTransitionError(
            machine=TASK_RUN_MACHINE.name,
            source=expected.value,
            target=target.value,
            allowed=(),
        )

    moment = _resolve_now(now)
    updates: dict[str, object] = {"state": target.value}

    if TASK_RUN_MACHINE.is_terminal(target):
        updates["finished_at"] = moment
        updates["lease_owner"] = None
        updates["lease_expires_at"] = None

    if target is TaskRunState.RETRYING:
        # A retry is a new attempt on the same row, so the attempt counter is
        # the only thing that distinguishes them, and the previous attempt's
        # finish time must be cleared or the row would claim to be finished.
        updates["attempt"] = F("attempt") + 1
        updates["finished_at"] = None
        updates["lease_owner"] = None
        updates["lease_expires_at"] = None

    return TaskRun.objects.filter(pk=task_run_id, state=expected.value).update(**updates) == 1


def claim_task_run(
    *,
    task_run_id: UUID,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> int | None:
    """Claim a READY task run, returning the new fencing token or ``None``.

    The fencing token is incremented inside the same statement that flips the
    state, so a token can never be handed to two workers: the ``UPDATE`` only
    matches while the row is still ``READY``.

    Returns:
        The fencing token this claim was granted, or ``None`` if another worker
        claimed first.

    Raises:
        ValueError: if ``worker_id`` is blank or ``lease_seconds`` is not
            positive. Both would produce a lease that cannot be enforced.
    """
    if not worker_id.strip():
        raise ValueError("worker_id must not be blank")
    if lease_seconds <= 0:
        raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")

    TASK_RUN_MACHINE.apply(TaskRunState.READY, TaskRunState.RUNNING)

    moment = _resolve_now(now)
    with transaction.atomic():
        claimed = TaskRun.objects.filter(
            pk=task_run_id,
            state=TaskRunState.READY.value,
        ).update(
            state=TaskRunState.RUNNING.value,
            fencing_token=F("fencing_token") + 1,
            lease_owner=worker_id,
            lease_expires_at=moment + timedelta(seconds=lease_seconds),
            # First claim sets started_at; a retry keeps the original, so
            # "time to first start" and "time to finish" stay meaningful.
            started_at=Coalesce(F("started_at"), Value(moment)),
        )
        if claimed != 1:
            return None
        # Safe inside the transaction: the row is locked by our UPDATE and is
        # no longer READY, so nothing can bump the token underneath us.
        return TaskRun.objects.values_list("fencing_token", flat=True).get(pk=task_run_id)


def finish_task_run(
    *,
    task_run_id: UUID,
    target: TaskRunState,
    fencing_token: int,
    now: datetime | None = None,
) -> bool:
    """Complete a running task run, proving ownership with its fencing token.

    This is where the fencing token earns its keep. A worker that was presumed
    dead, then resurrected, still holds an older token; when it finally writes,
    the ``fencing_token`` predicate matches nothing and the write is discarded
    instead of overwriting a newer attempt's result.

    Returns:
        ``True`` if this call wrote the outcome, ``False`` if the row had moved
        on (or the token was stale).

    Raises:
        IllegalTransitionError: if ``RUNNING -> target`` is not a declared edge.
        LeaseRequiredError: never, given a valid target; the lease is asserted here.
    """
    TASK_RUN_MACHINE.apply(TaskRunState.RUNNING, target, lease_held=True)

    # `finish_task_run` is the fencing-token (lease-requiring) path. The reaper
    # edge `RUNNING -> READY` does not require a lease and has its own side
    # effects (clearing the lease, bumping the token), so it must not be reachable
    # through this function. Rejecting it keeps "finishing" meaning "terminating a
    # task" and leaves reaping exclusively to `reclaim_task_run`.
    if not TASK_RUN_MACHINE.edge(TaskRunState.RUNNING, target).requires_lease:
        raise IllegalTransitionError(
            machine=TASK_RUN_MACHINE.name,
            source=TaskRunState.RUNNING.value,
            target=target.value,
            allowed=(
                t.value
                for t in TASK_RUN_MACHINE.targets(TaskRunState.RUNNING)
                if TASK_RUN_MACHINE.edge(TaskRunState.RUNNING, t).requires_lease
            ),
        )

    moment = _resolve_now(now)
    updates: dict[str, object] = {
        "state": target.value,
        "finished_at": moment,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    return (
        TaskRun.objects.filter(
            pk=task_run_id,
            state=TaskRunState.RUNNING.value,
            fencing_token=fencing_token,
        ).update(**updates)
        == 1
    )


def utc_now() -> datetime:
    """Timezone-aware current time, for tests that want a fixed reference."""
    return datetime.now(tz=UTC)
