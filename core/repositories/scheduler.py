"""The scheduler's eligibility logic — which tasks may run, and dispatching them.

This is the "which tasks dispatch" process from ADR-011. It does not talk to the
broker; it writes an outbox row (via :mod:`core.repositories.outbox`) so the
publisher can deliver it. Everything here runs inside one transaction per run, so the
eligibility work and the outbox write commit together — the dual-write remedy.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from croniter import croniter
from django.db import connection, transaction
from django.utils import timezone

from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.expressions import evaluate_condition
from core.domain.services import derive_workflow_run_state
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import (
    TASK_RUN_TERMINAL,
    ScheduledTrigger,
    Task,
    TaskRun,
    WorkflowRun,
)
from core.observability.tracing import (
    context_from_traceparent,
    current_traceparent,
    start_span,
)
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

logger = structlog.get_logger(__name__)


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
            # The dispatch span is a child of the run's stored trace context, so
            # the scheduler's work is attributed to the request that started the
            # run even though that request's process is long gone.
            with start_span(
                "task.dispatch",
                context=context_from_traceparent(run.traceparent),
                attributes={
                    "flowforge.organization_id": str(run.organization_id or ""),
                    "flowforge.workflow_run_id": str(run.id),
                    "flowforge.task_run_id": str(task_run.id),
                    "flowforge.task_key": task_run.task.key,
                },
            ):
                dispatch = create_dispatch(
                    task_run=task_run,
                    workflow_run=run,
                    fencing_token_snapshot=task_run.fencing_token,
                    attempt_snapshot=task_run.attempt,
                    correlation_id=run.correlation_id,
                    # Fall back to the run's context when tracing is disabled, so
                    # the worker still inherits the API trace.
                    traceparent=current_traceparent() or run.traceparent,
                )
            if dispatch is not None:
                enqueued += 1
    return enqueued


def _compensation_plan(
    definition: WorkflowDefinition, succeeded: set[str]
) -> list[tuple[str, str]]:
    """Ordered ``(target, compensator)`` pairs for succeeded tasks that have one.

    Reverse dependency order: the forward topological order puts every dependency
    before its dependants, so reversing it undoes dependents first — a task is
    compensated only after everything that depended on it has been compensated,
    which is what makes the rollback mirror the forward execution.
    """
    compensator_of = {
        spec.compensate_for: spec.key
        for spec in definition.tasks
        if spec.compensate_for is not None
    }
    return [
        (key, compensator_of[key])
        for key in reversed(definition.graph.topological_order())
        if key in succeeded and key in compensator_of
    ]


def _start_compensation(run: WorkflowRun, compensator_key: str) -> TaskRun:
    """Create the ``READY`` task run for one compensation and return it.

    A compensation task declares no dependencies (enforced at parse time), so it
    is eligible the moment it exists; the caller's ``enqueue_ready_tasks``
    dispatches it.
    """
    task = Task.objects.get(workflow=run.workflow, key=compensator_key)
    return TaskRun.objects.create(
        workflow_run=run,
        organization_id=run.organization_id,
        task=task,
        state=TaskRunState.READY.value,
    )


def _advance_compensation(*, run: WorkflowRun, definition: WorkflowDefinition) -> bool:
    """Drive the saga rollback one step, returning whether it has settled.

    Compensations run strictly one at a time, in reverse dependency order: the
    next compensator is only started once the previous one has *succeeded*, so
    the undo sequence is ordered without a stored plan. A compensator that fails
    or dies halts the rollback (logged), because continuing to undo later work
    after an earlier undo failed would leave the saga half-applied.

    Returns:
        ``True`` when the saga is settled — every planned compensator has
        resolved, there was nothing to compensate, or a compensator failed — so
        the caller may finalise the run to ``FAILED``. ``False`` while a
        compensator is still in flight or waiting its turn.
    """
    task_runs = {
        task_run.task.key: task_run
        for task_run in TaskRun.objects.filter(workflow_run=run).select_related("task")
    }
    succeeded = {key for key, tr in task_runs.items() if tr.state == TaskRunState.SUCCEEDED.value}
    plan = _compensation_plan(definition, succeeded)
    if not plan:
        return True

    for index, (_target, compensator) in enumerate(plan):
        existing = task_runs.get(compensator)
        if existing is None:
            previous = plan[index - 1][1] if index > 0 else None
            if previous is None or task_runs[previous].state == TaskRunState.SUCCEEDED.value:
                _start_compensation(run, compensator)
                logger.info(
                    "compensation_started",
                    run_id=str(run.id),
                    compensator=compensator,
                )
            return False
        if existing.state not in TASK_RUN_TERMINAL:
            return False  # still in flight (pending/ready/running/retrying/failed)
        if existing.state != TaskRunState.SUCCEEDED.value:
            # A compensator failed or died: stop the rollback rather than undo
            # later work on top of an incomplete earlier undo.
            logger.warning(
                "compensation_halted",
                run_id=str(run.id),
                compensator=compensator,
                state=existing.state,
            )
            return True
        # This compensator succeeded; fall through to the next in the plan.
    return True


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
        by_key = {tr.task.key: tr for tr in task_runs}

        promoted = 0
        # Iterate in dependency order, not in the order the database returned the
        # rows. PostgreSQL makes no ordering guarantee, so iterating the queryset
        # directly makes skip/promotion propagation order-dependent: a dependant
        # processed before the dependency it waits on would not yet see that
        # dependency skipped, and a single pass would leave it PENDING. The
        # topological order puts every dependency before its dependants, so one
        # pass fully propagates skips and promotions deterministically.
        for key in definition.graph.topological_order():
            task_run = by_key.get(key)
            if task_run is None or task_run.state != TaskRunState.PENDING.value:
                continue
            spec = specs[key]
            if _should_skip(spec, succeeded=succeeded, skipped=skipped, context=context):
                transition_task_run(
                    task_run_id=task_run.id,
                    expected=TaskRunState.PENDING,
                    target=TaskRunState.SKIPPED,
                    # The row is already in memory; passing its tenant avoids a
                    # SELECT per transition (see `transition_task_run`).
                    organization_id=task_run.organization_id,
                )
                skipped.add(key)
                continue
            if _is_eligible(spec, succeeded) and transition_task_run(
                task_run_id=task_run.id,
                expected=TaskRunState.PENDING,
                target=TaskRunState.READY,
                organization_id=task_run.organization_id,
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

        # Complete the run when its *forward* tasks have resolved. The scheduler
        # owns the run's lifecycle; a worker only owns one task run. The
        # precedence lives in `derive_workflow_run_state`, so it is not
        # re-implemented here. Compensation task runs are excluded: the run's
        # forward outcome is what decides failure, and compensations are cleanup
        # that runs afterward rather than tasks that change the outcome.
        forward_derived = derive_workflow_run_state(
            TaskRunState(task_run.state)
            for task_run in TaskRun.objects.filter(workflow_run=run).select_related("task")
            if task_run.task.compensate_for is None
        )

        # Saga rollback. A failed forward run triggers the compensations of the
        # tasks that had succeeded, in reverse dependency order, before the run is
        # allowed to finish. Done before the dispatch below so a freshly started
        # compensator (created READY) is enqueued in this same pass.
        settled = True
        if (
            run.state == WorkflowRunState.RUNNING.value
            and forward_derived is WorkflowRunState.FAILED
        ):
            settled = _advance_compensation(run=run, definition=definition)

        enqueued = enqueue_ready_tasks(run)

        if run.state == WorkflowRunState.RUNNING.value:
            if forward_derived is WorkflowRunState.SUCCEEDED:
                transition_workflow_run(
                    run_id=run.id,
                    expected=WorkflowRunState.RUNNING,
                    target=WorkflowRunState.SUCCEEDED,
                )
            elif forward_derived is WorkflowRunState.FAILED and settled:
                transition_workflow_run(
                    run_id=run.id,
                    expected=WorkflowRunState.RUNNING,
                    target=WorkflowRunState.FAILED,
                )
            elif forward_derived is WorkflowRunState.CANCELLED:
                transition_workflow_run(
                    run_id=run.id,
                    expected=WorkflowRunState.RUNNING,
                    target=WorkflowRunState.CANCELLED,
                )
        elif (
            run.state == WorkflowRunState.PENDING.value
            and forward_derived is WorkflowRunState.CANCELLED
        ):
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
    # Fire due scheduled triggers before advancing runs, so a trigger's fresh run
    # is eligible for scheduling in the very same pass rather than the next one.
    tick_scheduled_triggers()
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
    *, now: datetime | None = None, requested_by_id: int | None = None
) -> int:
    """Fire every due trigger exactly once, atomically claimed per instance.

    Each trigger is claimed with ``SELECT FOR UPDATE SKIP LOCKED`` so N
    scheduler instances polling the same table race safely: the first instance
    locks the row, advances ``next_run_at``, and triggers the workflow; the
    others skip locked rows and move on. No distributed lock, no coordination
    protocol — the database is the mutex.

    The claim-and-advance happens inside one transaction that is committed
    *before* any workflow is triggered. That ordering matters for two reasons:
    it releases the row lock so a slow trigger cannot block the others, and it
    means ``next_run_at`` is advanced before the trigger runs. If the trigger
    then raises, the clock already says "I fired at this time", so recovery is a
    missing run, never a double-fire.

    A trigger whose workflow has no tasks is a dangling reference (the workflow
    was never published). Such a trigger is advanced but not fired — the operator
    must fix the workflow, not the clock.

    Returns the number of workflows triggered in this tick.
    """
    # Deferred import: core.services.runs imports the core.repositories package,
    # whose __init__ imports this module, so a top-level import would be a cycle.
    from core.services.runs import trigger  # noqa: PLC0415

    moment = _resolve_now(now)
    triggered = 0

    # SKIP LOCKED is the no-double-fire mechanism on PostgreSQL. SQLite has no
    # row locks to skip, but it also serialises writers, so the unit tests that
    # run against it cannot race anyway; there the lock is advisory only.
    skip_locked = connection.vendor == "postgresql"

    # The ordering matters: oldest first, so a backlog is drained in sequence
    # rather than jumping around. The lock is held only across the clock advance;
    # `trigger` runs after the transaction commits.
    with transaction.atomic():
        due = list(
            ScheduledTrigger.objects.filter(enabled=True, next_run_at__lte=moment)
            .select_related("workflow", "workflow__organization")
            # Lock only the trigger rows. `workflow.organization` is nullable, so
            # the join is an OUTER join, and PostgreSQL refuses `FOR UPDATE` on the
            # nullable side; `of=("self",)` keeps the lock on the non-nullable side.
            # SQLite has no row locks and ignores this.
            .select_for_update(skip_locked=skip_locked, of=("self",))
            .order_by("next_run_at")
        )
        for scheduled in due:
            itr = croniter(scheduled.cron_expression, moment)
            next_dt = itr.get_next(datetime)
            # Older croniter builds can return a naive value; the field is
            # tz-aware, so a naive result would raise on save. Normalise.
            if timezone.is_naive(next_dt):
                next_dt = timezone.make_aware(next_dt, UTC)
            scheduled.next_run_at = next_dt
            scheduled.last_run_at = moment
            scheduled.save(update_fields=["next_run_at", "last_run_at"])

    # Outside the row lock: trigger each advanced workflow. A trigger that raises
    # must not abort the rest, and must not double-fire (the clock advanced above).
    for scheduled in due:
        # A workflow with no tasks was never published; advancing was enough.
        if not Task.objects.filter(workflow_id=scheduled.workflow_id).exists():
            logger.warning(
                "scheduled_trigger_unpublished",
                trigger_id=str(scheduled.id),
                workflow_id=str(scheduled.workflow_id),
            )
            continue
        try:
            trigger(
                workflow=scheduled.workflow,
                requested_by_id=requested_by_id,
                organization=scheduled.workflow.organization,
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
