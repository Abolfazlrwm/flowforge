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
from core.domain.retry import calculate_backoff, get_retry_policy
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
    """Reclaim a RUNNING task run whose lease has expired, routing it by policy.

    This is the lease reaper's per-row operation. A dead worker counts as a failed
    attempt, so the row is routed into the retry lifecycle rather than simply
    returned to ``READY``:

    * **Attempts remain** (``attempt < max_attempts``): ``RUNNING -> RETRYING`` with
      the attempt counter incremented and a backoff delay written to
      ``scheduled_at``. The scheduler later promotes it back to ``READY`` once the
      delay elapses.
    * **Policy exhausted** (``attempt >= max_attempts``): ``RUNNING -> DEAD``. The
      task is finished and must be requeued by hand (see :func:`requeue_dead_task_run`).

    The conditional ``UPDATE`` matches only while the row is still ``RUNNING`` *and*
    its lease has already expired, so a live lease is never reclaimed and a row
    another reaper just reclaimed is never reclaimed twice.

    Reclaiming bumps the fencing token. The worker that held the expired lease may
    still be alive and may try to ``finish_task_run``; its token is now stale (the
    row is no longer ``RUNNING``, and even a later reclaim increments the token
    again), so its write matches zero rows. The reaper is what makes a resurrected
    worker harmless without the broker ever knowing one existed.

    Returns:
        ``True`` if this call reclaimed the row, ``False`` if the row was gone,
        no longer running, or its lease had not yet expired.
    """
    moment = _resolve_now(now)
    with transaction.atomic():
        task_run = (
            TaskRun.objects.select_for_update()
            .select_related("task")
            .filter(pk=task_run_id, state=TaskRunState.RUNNING.value, lease_expires_at__lt=moment)
            .first()
        )
        if task_run is None:
            return False

        policy = get_retry_policy(task_run.task.config)
        attempt = task_run.attempt
        if attempt < policy.max_attempts:
            TASK_RUN_MACHINE.apply(TaskRunState.RUNNING, TaskRunState.RETRYING)
            next_attempt = attempt + 1
            scheduled_at = moment + timedelta(
                seconds=calculate_backoff(attempt=next_attempt, policy=policy)
            )
            updates: dict[str, object] = {
                "state": TaskRunState.RETRYING.value,
                "attempt": next_attempt,
                "scheduled_at": scheduled_at,
                "lease_owner": None,
                "lease_expires_at": None,
                "fencing_token": F("fencing_token") + 1,
                "started_at": Coalesce(F("started_at"), Value(moment)),
            }
        else:
            TASK_RUN_MACHINE.apply(TaskRunState.RUNNING, TaskRunState.DEAD)
            # DEAD is terminal: record the finish time and clear the lease.
            updates = {
                "state": TaskRunState.DEAD.value,
                "finished_at": moment,
                "lease_owner": None,
                "lease_expires_at": None,
                "scheduled_at": None,
                "fencing_token": F("fencing_token") + 1,
                "started_at": Coalesce(F("started_at"), Value(moment)),
            }
        reclaimed = TaskRun.objects.filter(pk=task_run_id).update(**updates)
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


def schedule_retries(*, now: datetime | None = None, limit: int = 1000) -> int:
    """Route every ``FAILED`` task run into the retry lifecycle, by policy.

    A failed task is not terminal: it is either scheduled for another attempt
    (``FAILED -> RETRYING`` with a backoff delay in ``scheduled_at``) while
    attempts remain, or buried (``FAILED -> DEAD``) once the policy is exhausted.
    The scheduler's :func:`promote_due_retries` later turns the ``RETRYING`` rows
    back into ``READY`` once their delay elapses.

    Returns:
        The number of task runs moved in this pass (retried + buried).
    """
    moment = _resolve_now(now)
    failed = list(
        TaskRun.objects.filter(state=TaskRunState.FAILED.value)
        .select_related("task")
        .order_by("id")[:limit]
    )
    moved = 0
    with transaction.atomic():
        for task_run in failed:
            policy = get_retry_policy(task_run.task.config)
            if task_run.attempt < policy.max_attempts:
                scheduled_at = moment + timedelta(
                    seconds=calculate_backoff(attempt=task_run.attempt + 1, policy=policy)
                )
                if transition_task_run(
                    task_run_id=task_run.id,
                    expected=TaskRunState.FAILED,
                    target=TaskRunState.RETRYING,
                    now=moment,
                    scheduled_at=scheduled_at,
                ):
                    moved += 1
            elif transition_task_run(
                task_run_id=task_run.id,
                expected=TaskRunState.FAILED,
                target=TaskRunState.DEAD,
                now=moment,
            ):
                moved += 1
    return moved


def promote_due_retries(*, now: datetime | None = None, limit: int = 1000) -> int:
    """Promote ``RETRYING`` task runs whose backoff has elapsed back to ``READY``.

    A ``RETRYING`` row carries the earliest moment it may run again in
    ``scheduled_at``; once that moment has passed it is eligible to be dispatched
    again. Promoting it to ``READY`` lets the scheduler re-enqueue it as a fresh
    generation.

    Returns:
        The number of task runs promoted to ``READY`` in this pass.
    """
    moment = _resolve_now(now)
    due = list(
        TaskRun.objects.filter(
            state=TaskRunState.RETRYING.value,
            scheduled_at__lte=moment,
        ).order_by("id")[:limit]
    )
    promoted = 0
    with transaction.atomic():
        for task_run in due:
            if transition_task_run(
                task_run_id=task_run.id,
                expected=TaskRunState.RETRYING,
                target=TaskRunState.READY,
                now=moment,
            ):
                promoted += 1
    return promoted


#: States a cancellation may act on. A terminal row is already resolved, so
#: cancelling it would either be a no-op or would overwrite a real outcome.
CANCELLABLE_TASK_STATES = (
    TaskRunState.PENDING.value,
    TaskRunState.READY.value,
    TaskRunState.RUNNING.value,
    TaskRunState.RETRYING.value,
)


def cancel_task_run(*, task_run_id: UUID, now: datetime | None = None) -> bool:
    """Cancel a task run, whoever holds its lease.

    Cancellation is an *operator* action, not a lease-holder action, so it cannot
    go through :func:`finish_task_run` — the caller has no fencing token to prove
    ownership with. It also cannot go through :func:`transition_task_run`, whose
    contract deliberately refuses lease edges.

    This is the same shape as the reaper: one conditional ``UPDATE`` that matches
    only while the row is still cancellable, clearing the lease and bumping the
    fencing token. Bumping the token is what makes cancellation *cooperative*: a
    handler that is still running will try to finish, and its write matches zero
    rows, so its result is discarded rather than resurrecting a cancelled task.

    ``RETRYING`` is cancellable too — a retry waiting out its backoff must not be
    promoted after the run was cancelled.

    Returns:
        ``True`` if this call cancelled the row, ``False`` if it was gone or
        already terminal.
    """
    moment = _resolve_now(now)
    return (
        TaskRun.objects.filter(pk=task_run_id, state__in=CANCELLABLE_TASK_STATES).update(
            state=TaskRunState.CANCELLED.value,
            finished_at=moment,
            lease_owner=None,
            lease_expires_at=None,
            scheduled_at=None,
            fencing_token=F("fencing_token") + 1,
        )
        == 1
    )


def requeue_dead_task_run(*, task_run_id: UUID, now: datetime | None = None) -> bool:
    """Manually requeue a ``DEAD`` task run for another attempt (dead-letter recovery).

    A ``DEAD`` task run is terminal: the retry policy was exhausted and an operator
    must intervene. This is that intervention. It resets the row to ``PENDING`` (a
    fresh attempt: ``attempt = 1``, cleared finish/lease/scheduled/result) and bumps
    the fencing token so any in-flight reference to the old generation is
    invalidated. It also reverts the parent workflow run to ``PENDING`` so the
    scheduler re-evaluates it on the next pass.

    Returns:
        ``True`` if this call requeued the row, ``False`` if it was gone or not
        ``DEAD`` (so nothing to do).
    """
    _resolve_now(now)
    with transaction.atomic():
        task_run = (
            TaskRun.objects.select_for_update()
            .select_related("workflow_run")
            .filter(pk=task_run_id, state=TaskRunState.DEAD.value)
            .first()
        )
        if task_run is None:
            return False
        TaskRun.objects.filter(pk=task_run_id).update(
            state=TaskRunState.PENDING.value,
            attempt=1,
            finished_at=None,
            scheduled_at=None,
            lease_owner=None,
            lease_expires_at=None,
            result=None,
            fencing_token=F("fencing_token") + 1,
        )
        # The run may already be FAILED (because this task was DEAD). Revert it to
        # PENDING; advance_workflow_run re-promotes it and re-derives its state,
        # leaving SUCCEEDED siblings untouched.
        WorkflowRun.objects.filter(pk=task_run.workflow_run_id).update(
            state=WorkflowRunState.PENDING.value,
            started_at=None,
            finished_at=None,
        )
        return True


def transition_task_run(
    *,
    task_run_id: UUID,
    expected: TaskRunState,
    target: TaskRunState,
    now: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> bool:
    """Move a task run between non-lease states, if it is still in ``expected``.

    This deliberately does **not** handle the transitions that require a lease
    (those go through :func:`finish_task_run`, which proves ownership with a
    fencing token) nor the reaper edges out of ``RUNNING`` (those go through
    :func:`reclaim_task_run`, which clears the lease and bumps the token).

    Returns:
        ``True`` if this call performed the transition, ``False`` otherwise.

    Raises:
        IllegalTransitionError: if ``expected -> target`` is not a declared edge,
            or it is a reaper edge that must go through ``reclaim_task_run``.
        LeaseRequiredError: if the edge requires a held lease.
    """
    TASK_RUN_MACHINE.apply(expected, target)

    # The reaper edges out of RUNNING (`reap_retry` -> RETRYING, `reap_dead` ->
    # DEAD) require reaper-side effects — clearing the lease and bumping the
    # fencing token — that this function does not perform, so the reaper owns
    # them exclusively via `reclaim_task_run`. Every other RUNNING edge is a
    # lease transition and must go through `finish_task_run` (which raises
    # LeaseRequiredError for it, since `apply` above ran without a token).
    if (
        expected is TaskRunState.RUNNING
        and not TASK_RUN_MACHINE.edge(TaskRunState.RUNNING, target).requires_lease
    ):
        raise IllegalTransitionError(
            machine=TASK_RUN_MACHINE.name,
            source=expected.value,
            target=target.value,
            allowed=(),
        )

    moment = _resolve_now(now)
    updates: dict[str, object] = {
        "state": target.value,
        # `scheduled_at` only has meaning while the row is RETRYING; the CHECK
        # constraint `taskrun_scheduled_iff_retrying` enforces exactly that. Clear
        # it by default so *every* exit from RETRYING (promotion to READY, or a
        # cancel) satisfies the constraint; the RETRYING branch below overwrites it.
        "scheduled_at": None,
    }

    if TASK_RUN_MACHINE.is_terminal(target):
        updates["finished_at"] = moment
        updates["lease_owner"] = None
        updates["lease_expires_at"] = None

    if target is TaskRunState.RETRYING:
        # A retry is a new attempt on the same row, so the attempt counter is
        # the only thing that distinguishes them, and the previous attempt's
        # finish time must be cleared or the row would claim to be finished. The
        # backoff delay lives in `scheduled_at` until `promote_due_retries`
        # promotes the row back to READY.
        updates["attempt"] = F("attempt") + 1
        updates["finished_at"] = None
        updates["lease_owner"] = None
        updates["lease_expires_at"] = None
        updates["scheduled_at"] = scheduled_at if scheduled_at is not None else moment

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
    result: object | None = None,
    now: datetime | None = None,
) -> bool:
    """Complete a running task run, proving ownership with its fencing token.

    This is where the fencing token earns its keep. A worker that was presumed
    dead, then resurrected, still holds an older token; when it finally writes,
    the ``fencing_token`` predicate matches nothing and the write is discarded
    instead of overwriting a newer attempt's result.

    ``result`` is the handler's durable return value, persisted only on success
    (and ignored when ``None`` so a failed attempt leaves the column untouched for
    the next attempt to overwrite). Idempotent handlers reuse a prior row's
    ``result`` via the ``idempotency_key`` column — see :mod:`core.worker`.

    Returns:
        ``True`` if this call wrote the outcome, ``False`` if the row had moved
        on (or the token was stale).

    Raises:
        IllegalTransitionError: if ``RUNNING -> target`` is not a declared edge.
        LeaseRequiredError: never, given a valid target; the lease is asserted here.
    """
    TASK_RUN_MACHINE.apply(TaskRunState.RUNNING, target, lease_held=True)

    # `finish_task_run` is the fencing-token (lease-requiring) path. The reaper
    # edges `RUNNING -> RETRYING` and `RUNNING -> DEAD` do not require a lease and
    # have their own side effects (clearing the lease, bumping the token), so they
    # must not be reachable through this function. Rejecting them keeps "finishing"
    # meaning "terminating a task" and leaves reaping exclusively to
    # `reclaim_task_run`.
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
    if result is not None:
        updates["result"] = result
    return (
        TaskRun.objects.filter(
            pk=task_run_id,
            state=TaskRunState.RUNNING.value,
            fencing_token=fencing_token,
        ).update(**updates)
        == 1
    )


def renew_lease(
    *,
    task_run_id: UUID,
    fencing_token: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Extend a running task run's lease, proving ownership with its token.

    The worker's heartbeat calls this while a handler runs, so a long task is not
    reaped mid-flight. It matches only while the row is still ``RUNNING`` and still
    owned by ``fencing_token``: once the reaper has bumped the token, the dead
    worker cannot extend a lease it no longer holds.

    Returns:
        ``True`` if the lease was extended, ``False`` if the row moved on.

    Raises:
        ValueError: if ``lease_seconds`` is not positive.
    """
    if lease_seconds <= 0:
        raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
    moment = _resolve_now(now)
    return (
        TaskRun.objects.filter(
            pk=task_run_id,
            state=TaskRunState.RUNNING.value,
            fencing_token=fencing_token,
        ).update(lease_expires_at=moment + timedelta(seconds=lease_seconds))
        == 1
    )


def utc_now() -> datetime:
    """Timezone-aware current time, for tests that want a fixed reference."""
    return datetime.now(tz=UTC)
