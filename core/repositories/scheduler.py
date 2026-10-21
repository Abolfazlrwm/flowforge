"""The scheduler's eligibility logic — which tasks may run, and dispatching them.

This is the "which tasks dispatch" process from ADR-011. It does not talk to the
broker; it writes an outbox row (via :mod:`core.repositories.outbox`) so the
publisher can deliver it. Everything here runs inside one transaction per run, so the
eligibility work and the outbox write commit together — the dual-write remedy.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import structlog
from croniter import croniter
from django.db import transaction

logger = structlog.get_logger(__name__)

from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.expressions import evaluate_condition
from core.domain.services import derive_workflow_run_state
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import ScheduledTrigger, TaskRun, WorkflowRun
from core.repositories.definitions import load_definition
from core.repositories.outbox import create_dispatch
from core.repositories.transitions import (
    _resolve_now,
    promote_due_retries,
    reap_expired_leases,
    schedule_retries,
    transition_task_run,
    transition_workflow_run,
)


def _is_eligible(spec: TaskDefinition, succeeded: set[str]) -> bool:
    """Whether ``spec``'s dependencies are satisfied, honouring ``join``.

    ``all`` is the default and the classic fan-in: every dependency must have
    succeeded. ``any`` is for alternative branches — the task runs once *one*
    upstream path completed, which is what a fan-in over "either A or B" means.
    """
    if spec.join == "any":
        return any(dependency in succeeded for dependency in spec.depends_on)
    return all(dependency in succeeded for dependency in spec.depends_on)


def _should_skip(
    spec: TaskDefinition,
    *,
    succeeded: set[str],
    skipped: set[str],
    context: dict[str, object],
) -> bool:
    """Whether ``spec`` must be skipped rather than run.

    Two distinct reasons, checked in this order:

    1. **The condition is false.** ``when`` is evaluated against the run's task
       results; a false condition means "this branch is not taken".
    2. **A dependency was skipped.** A branch that was not taken cannot feed the
       tasks below it, so the skip propagates instead of leaving them ``PENDING``
       forever — which would stall the run rather than finish it.

    For ``join="any"`` the propagation is weaker: the task is skipped only when
    *every* dependency was skipped, because one surviving path is enough.

    Raises:
        ExpressionError: if the condition names something the context lacks. A
            broken condition is a definition bug, not a silent skip.
    """
    if spec.when is not None and not evaluate_condition(spec.when, context):
        return True
    if spec.join == "any":
        if any(dependency in succeeded for dependency in spec.depends_on):
            return False
        return all(dependency in skipped for dependency in spec.depends_on)
    return any(dependency in skipped for dependency in spec.depends_on)


def enqueue_ready_tasks(run: WorkflowRun) -> int:
    """Create one outbox row per READY task run that lacks one for its generation.

    Opens its own transaction (a savepoint when the caller already has one) so
    ``select_for_update`` is always legal — PostgreSQL rejects it outside a
    transaction, and a caller that forgot one would otherwise fail only in
    production. Locks the READY rows it touches so two schedulers cannot both
    dispatch the same task. Returns the number of rows created (not the number
    considered).
    """
    enqueued = 0
    with transaction.atomic():
        ready = list(
            TaskRun.objects.select_for_update()
            .filter(workflow_run=run, state=TaskRunState.READY.value)
            .select_related("task")
        )
        for task_run in ready:
            if (
                create_dispatch(
                    task_run=task_run,
                    workflow_run=run,
                    fencing_token_snapshot=task_run.fencing_token,
                    attempt_snapshot=task_run.attempt,
                )
                is not None
            ):
                enqueued += 1
    return enqueued


def advance_workflow_run(run: WorkflowRun) -> tuple[int, int]:
    """Promote eligible pending tasks and dispatch the ready ones.

    Args:
        run: The workflow run to advance.

    Returns:
        ``(promoted, enqueued)`` — the number of PENDING tasks moved to READY, and
        the number of outbox rows created.

    The whole method is one transaction: a run that is PENDING and has live work is
    moved to RUNNING, its dependency-satisfied tasks are promoted, and their dispatch
    events are written to the outbox. A crash at any point leaves a consistent state —
    either the row and its outbox entry both exist, or neither does.
    """
    with transaction.atomic():
        run = WorkflowRun.objects.select_for_update().get(pk=run.pk)
        if run.state not in (WorkflowRunState.PENDING.value, WorkflowRunState.RUNNING.value):
            return (0, 0)

        definition: WorkflowDefinition = load_definition(run.workflow)
        # Force the graph now: a definition that slipped through publish-time
        # validation must fail here, attributed to this run, not to a query later.
        definition.graph  # noqa: B018 - forcing the cached property is the point

        task_runs = list(TaskRun.objects.filter(workflow_run=run).select_related("task"))
        succeeded = {tr.task.key for tr in task_runs if tr.state == TaskRunState.SUCCEEDED.value}
        skipped = {tr.task.key for tr in task_runs if tr.state == TaskRunState.SKIPPED.value}
        # The condition context is the run's *results*, keyed by task. Only tasks
        # that produced one appear, so `tasks.extract.amount` is a meaningful
        # lookup and a missing key is a loud error rather than a silent false.
        context: dict[str, object] = {
            "tasks": {tr.task.key: tr.result for tr in task_runs if tr.result is not None}
        }
        specs = {spec.key: spec for spec in definition.tasks}

        promoted = 0
        for task_run in (tr for tr in task_runs if tr.state == TaskRunState.PENDING.value):
            spec = specs[task_run.task.key]
            if _should_skip(spec, succeeded=succeeded, skipped=skipped, context=context):
                transition_task_run(
                    task_run_id=task_run.id,
                    expected=TaskRunState.PENDING,
                    target=TaskRunState.SKIPPED,
                )
                skipped.add(spec.key)
                continue
            if _is_eligible(spec, succeeded) and transition_task_run(
                task_run_id=task_run.id,
                expected=TaskRunState.PENDING,
                target=TaskRunState.READY,
            ):
                promoted += 1

        # Start the run as soon as it has any live work to do. A run whose every
        # task is skipped has no live work, but it still has to *start* before it
        # can complete — the machine has no PENDING->SUCCEEDED edge, and inventing
        # one would be a second way to finish a run that never ran.
        if (
            run.state == WorkflowRunState.PENDING.value
            and (
                promoted > 0
                or skipped
                or TaskRun.objects.filter(
                    workflow_run=run,
                    state__in=[TaskRunState.READY.value, TaskRunState.RUNNING.value],
                ).exists()
            )
            and transition_workflow_run(
                run_id=run.id,
                expected=WorkflowRunState.PENDING,
                target=WorkflowRunState.RUNNING,
            )
        ):
            # The in-memory row still says PENDING; the completion check below
            # must see RUNNING or it would skip a run that just started.
            run.refresh_from_db()

        enqueued = enqueue_ready_tasks(run)

        # Complete the run when its tasks have resolved. The scheduler owns the
        # run's lifecycle; a worker only owns one task run. The precedence lives
        # in `derive_workflow_run_state`, so it is not re-implemented here.
        derived = derive_workflow_run_state(
            TaskRunState(task_run.state) for task_run in TaskRun.objects.filter(workflow_run=run)
        )
        if run.state == WorkflowRunState.RUNNING.value:
            if derived is WorkflowRunState.SUCCEEDED:
                transition_workflow_run(
                    run_id=run.id,
                    expected=WorkflowRunState.RUNNING,
                    target=WorkflowRunState.SUCCEEDED,
                )
            elif derived is WorkflowRunState.FAILED:
                transition_workflow_run(
                    run_id=run.id,
                    expected=WorkflowRunState.RUNNING,
                    target=WorkflowRunState.FAILED,
                )
            elif derived is WorkflowRunState.CANCELLED:
                transition_workflow_run(
                    run_id=run.id,
                    expected=WorkflowRunState.RUNNING,
                    target=WorkflowRunState.CANCELLED,
                )
        elif run.state == WorkflowRunState.PENDING.value and derived is WorkflowRunState.CANCELLED:
            transition_workflow_run(
                run_id=run.id,
                expected=WorkflowRunState.PENDING,
                target=WorkflowRunState.CANCELLED,
            )

    return (promoted, enqueued)


def schedule_once() -> tuple[int, int]:
    """One scheduler pass: reclaim, retry, promote, then advance every active run.

    The order matters. Reaping an expired lease turns a ``RUNNING`` task into
    ``RETRYING`` or ``DEAD``. ``schedule_retries`` then turns every ``FAILED`` task
    into ``RETRYING`` (if attempts remain) or ``DEAD`` (if exhausted). ``promote_due_retries``
    flips ``RETRYING`` rows whose backoff has elapsed back to ``READY`` — rows created
    in *this* pass carry a future ``scheduled_at`` and are left for a later pass, so
    the backoff is genuinely honoured. Only then are active runs advanced and their
    ``READY`` tasks dispatched.

    Returns ``(reaped, enqueued)``. A long-running scheduler command calls this in a
    loop; tests call it directly for a single, deterministic pass.
    """
    reaped = reap_expired_leases()
    schedule_retries()
    promoted = promote_due_retries()
    enqueued = 0
    for run in WorkflowRun.objects.filter(
        state__in=[WorkflowRunState.PENDING.value, WorkflowRunState.RUNNING.value]
    ):
        _, run_enqueued = advance_workflow_run(run)
        enqueued += run_enqueued
    # `promoted` is reported for observability alongside `reaped`; it does not
    # change dispatch counts (the promoted rows are counted as `enqueued` above).
    del promoted
    return (reaped, enqueued)


def tick_scheduled_triggers(
    *, now: datetime | None = None, user: Any | None = None
) -> int:
    """Fire every due trigger exactly once, atomically claimed per instance.

    Each trigger is claimed with ``SELECT FOR UPDATE SKIP LOCKED`` so N
    scheduler instances polling the same table race safely: the first instance
    locks the row, advances ``next_run_at``, and triggers the workflow; the
    others skip locked rows and move on. No distributed lock, no coordination
    protocol — the database is the mutex.

    ``next_run_at`` is advanced *before* the workflow is triggered. If the
    trigger crashes mid-flight, the trigger already says "I fired at this time";
    the operator must inspect the run, not the clock.

    Returns the number of workflows triggered in this tick.
    """
    from core.services.runs import trigger

    moment = _resolve_now(now)
    triggered = 0

    # The ordering matters: oldest first, so a backlog is drained in sequence
    # rather than jumping around. SKIP LOCKED is the no-double-fire mechanism.
    due = (
        ScheduledTrigger.objects.filter(enabled=True, next_run_at__lte=moment)
        .select_for_update(skip_locked=True)
        .order_by("next_run_at")
    )

    for scheduled in due:
        # Advance the clock before triggering, so a crash during trigger does
        # not produce a double-fire on recovery.
        itr = croniter(scheduled.cron_expression, moment)
        scheduled.next_run_at = itr.get_next(datetime)
        scheduled.last_run_at = moment
        scheduled.save(update_fields=["next_run_at", "last_run_at"])

        try:
            trigger(
                workflow=scheduled.workflow,
                requested_by=user,
            )
        except Exception:
            # The trigger advanced, so this failure is a missing run, not a
            # double-fire. Log and continue so one broken trigger does not
            # stall every other scheduled workflow.
            logger.exception(
                "scheduled_trigger_failed",
                trigger_id=str(scheduled.id),
                workflow_id=str(scheduled.workflow_id),
            )
            continue

        triggered += 1

    return triggered
