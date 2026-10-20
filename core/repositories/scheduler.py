"""The scheduler's eligibility logic — which tasks may run, and dispatching them.

This is the "which tasks dispatch" process from ADR-011. It does not talk to the
broker; it writes an outbox row (via :mod:`core.repositories.outbox`) so the
publisher can deliver it. Everything here runs inside one transaction per run, so the
eligibility work and the outbox write commit together — the dual-write remedy.
"""

from __future__ import annotations

from django.db import transaction

from core.domain.definition import WorkflowDefinition
from core.domain.services import derive_workflow_run_state
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun, WorkflowRun
from core.repositories.definitions import load_definition
from core.repositories.outbox import create_dispatch
from core.repositories.transitions import (
    reap_expired_leases,
    transition_task_run,
    transition_workflow_run,
)


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
        graph = definition.graph
        succeeded = {
            tr.task.key
            for tr in TaskRun.objects.filter(workflow_run=run)
            if tr.state == TaskRunState.SUCCEEDED.value
        }

        promoted = 0
        for task_run in TaskRun.objects.filter(workflow_run=run, state=TaskRunState.PENDING.value):
            # `is_ready` ignores task state and trusts only completed keys, so a key
            # already listed as done is skipped rather than re-dispatched. The
            # transition only fires when the dependency is satisfied (short-circuit),
            # so promoting and counting stay in lock-step.
            if graph.is_ready(task_run.task.key, succeeded) and transition_task_run(
                task_run_id=task_run.id,
                expected=TaskRunState.PENDING,
                target=TaskRunState.READY,
            ):
                promoted += 1

        # Start the run as soon as it has any live work to do.
        if (
            run.state == WorkflowRunState.PENDING.value
            and (
                promoted > 0
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
    """One scheduler pass: reclaim expired leases, then advance every active run.

    Returns ``(reaped, enqueued)``. A long-running scheduler command calls this in a
    loop; tests call it directly for a single, deterministic pass.
    """
    reaped = reap_expired_leases()
    enqueued = 0
    for run in WorkflowRun.objects.filter(
        state__in=[WorkflowRunState.PENDING.value, WorkflowRunState.RUNNING.value]
    ):
        _, run_enqueued = advance_workflow_run(run)
        enqueued += run_enqueued
    return (reaped, enqueued)
