"""The scheduler: promote eligible tasks, dispatch ready ones, once per generation.

These run on SQLite — the eligibility logic and the one-row-per-generation rule are
pure database operations, not broker operations, so they do not need PostgreSQL. The
SKIP LOCKED / concurrency behaviour is covered separately in the integration suite.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import OutboxMessage, Task, TaskRun, Workflow, WorkflowRun
from core.repositories.scheduler import (
    advance_workflow_run,
    enqueue_ready_tasks,
    schedule_once,
)
from core.repositories.transitions import claim_task_run, finish_task_run, reclaim_task_run
from django.core.management import call_command

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


def _scenario(name: str = "etl") -> tuple[Workflow, Task, Task, WorkflowRun, TaskRun, TaskRun]:
    """A two-task run: ``extract`` (no deps, READY) and ``load`` (depends on extract)."""
    workflow = Workflow.objects.create(name=name, version=1)
    extract = Task.objects.create(workflow=workflow, key="extract", task_type="extract")
    load = Task.objects.create(
        workflow=workflow, key="load", task_type="load", depends_on=["extract"]
    )
    run = WorkflowRun.objects.create(workflow=workflow, state=WorkflowRunState.PENDING.value)
    tr_extract = TaskRun.objects.create(
        workflow_run=run, task=extract, state=TaskRunState.READY.value
    )
    tr_load = TaskRun.objects.create(workflow_run=run, task=load, state=TaskRunState.PENDING.value)
    return workflow, extract, load, run, tr_extract, tr_load


def _succeed(task_run: TaskRun) -> None:
    """Claim and finish a task run so its dependencies become eligible."""
    token = claim_task_run(task_run_id=task_run.id, worker_id="w1", lease_seconds=30, now=NOW)
    assert token is not None
    assert finish_task_run(
        task_run_id=task_run.id,
        target=TaskRunState.SUCCEEDED,
        fencing_token=token,
        now=NOW,
    )


def test_first_pass_dispatches_the_ready_task_and_starts_the_run() -> None:
    """A run with a ready task begins running, and that task is dispatched once."""
    *_unused, run, tr_extract, tr_load = _scenario()

    promoted, enqueued = advance_workflow_run(run)

    assert promoted == 0  # load has an unmet dependency
    assert enqueued == 1  # extract is ready and gets an outbox row
    assert OutboxMessage.objects.count() == 1

    message = OutboxMessage.objects.get()
    assert message.payload["task_key"] == "extract"
    assert message.task_run_id == tr_extract.id

    run.refresh_from_db()
    assert run.state == WorkflowRunState.RUNNING.value
    tr_load.refresh_from_db()
    assert tr_load.state == TaskRunState.PENDING.value


def test_dependency_satisfied_promotes_and_dispatches() -> None:
    """Once extract succeeds, load becomes ready and is dispatched exactly once."""
    *_unused, run, tr_extract, tr_load = _scenario()
    advance_workflow_run(run)
    _succeed(tr_extract)

    promoted, enqueued = advance_workflow_run(run)

    assert promoted == 1
    assert enqueued == 1
    tr_load.refresh_from_db()
    assert tr_load.state == TaskRunState.READY.value
    # extract (from the first pass) + load (from this pass).
    assert OutboxMessage.objects.count() == 2


def test_repeated_passes_do_not_duplicate_dispatches() -> None:
    """Idempotency: a task already dispatched for its generation is not re-enqueued."""
    *_unused, run, tr_extract, _tr_load = _scenario()

    advance_workflow_run(run)
    advance_workflow_run(run)  # second pass, nothing new to do
    assert OutboxMessage.objects.count() == 1

    _succeed(tr_extract)
    advance_workflow_run(run)
    advance_workflow_run(run)  # idempotent second pass
    assert OutboxMessage.objects.count() == 2


def test_enqueue_ready_tasks_is_idempotent_per_generation() -> None:
    """Calling it twice for the same live generation yields one row."""
    *_unused, run, _tr_extract, _tr_load = _scenario()
    advance_workflow_run(run)  # enqueues extract

    again = enqueue_ready_tasks(run)
    assert again == 0
    assert OutboxMessage.objects.count() == 1


def test_a_reaped_lease_gets_a_new_generation_row() -> None:
    """Re-dispatch after a reaper pass is a new generation and a new outbox row.

    The scheduler bumps the fencing token on every reaper pass, so the re-dispatched
    generation is distinct and must not collide with the original row.
    """
    *_unused, run, tr_extract, _tr_load = _scenario()
    advance_workflow_run(run)  # enqueues extract (generation token 0)

    token = claim_task_run(task_run_id=tr_extract.id, worker_id="w1", lease_seconds=30, now=NOW)
    assert token == 1
    TaskRun.objects.filter(pk=tr_extract.id).update(lease_expires_at=NOW - timedelta(seconds=1))
    assert reclaim_task_run(task_run_id=tr_extract.id, now=NOW + timedelta(seconds=1))
    tr_extract.refresh_from_db()
    assert tr_extract.state == TaskRunState.READY.value
    assert tr_extract.fencing_token == 2  # reaper invalidated the dead worker's token

    enqueued = enqueue_ready_tasks(run)
    assert enqueued == 1  # new generation (token 2) gets a fresh row

    snapshots = set(
        OutboxMessage.objects.filter(task_run=tr_extract).values_list(
            "fencing_token_snapshot", flat=True
        )
    )
    assert snapshots == {0, 2}


def test_run_succeeds_when_all_tasks_succeed() -> None:
    """Once every task is SUCCEEDED, the scheduler completes the run."""
    *_unused, run, tr_extract, tr_load = _scenario()

    advance_workflow_run(run)  # dispatch extract, start the run
    _succeed(tr_extract)
    advance_workflow_run(run)  # promote load, dispatch
    _succeed(tr_load)
    advance_workflow_run(run)  # all tasks resolved -> complete the run

    run.refresh_from_db()
    assert run.state == WorkflowRunState.SUCCEEDED.value
    assert run.finished_at is not None


def test_run_fails_when_a_task_fails() -> None:
    """A failed task fails the run, even while another task is still pending."""
    *_unused, run, tr_extract, _tr_load = _scenario()
    advance_workflow_run(run)

    token = claim_task_run(task_run_id=tr_extract.id, worker_id="w1", lease_seconds=30, now=NOW)
    assert token is not None
    assert finish_task_run(
        task_run_id=tr_extract.id,
        target=TaskRunState.FAILED,
        fencing_token=token,
        now=NOW,
    )

    advance_workflow_run(run)

    run.refresh_from_db()
    assert run.state == WorkflowRunState.FAILED.value
    assert run.finished_at is not None


def test_schedule_once_advances_every_active_run() -> None:
    """The loop body advances all active runs, not just one."""
    _scenario(name="a")  # run A, own workflow
    _scenario(name="b")  # run B, independent workflow
    assert WorkflowRun.objects.filter(state=WorkflowRunState.PENDING.value).count() == 2

    reaped, enqueued = schedule_once()

    assert reaped == 0
    assert enqueued == 2  # one dispatch per run's ready task
    assert OutboxMessage.objects.count() == 2
    assert WorkflowRun.objects.filter(state=WorkflowRunState.RUNNING.value).count() == 2


def test_schedule_once_ignores_terminal_runs() -> None:
    """A succeeded run is not eligible for scheduling."""
    workflow = Workflow.objects.create(name="done", version=1)
    Task.objects.create(workflow=workflow, key="only", task_type="only")
    run = WorkflowRun.objects.create(
        workflow=workflow,
        state=WorkflowRunState.SUCCEEDED.value,
        finished_at=NOW,
    )
    TaskRun.objects.create(
        workflow_run=run,
        task=Task.objects.first(),
        state=TaskRunState.SUCCEEDED.value,
        finished_at=NOW,
    )

    reaped, enqueued = schedule_once()

    assert reaped == 0
    assert enqueued == 0
    assert OutboxMessage.objects.count() == 0


def test_scheduler_command_single_pass() -> None:
    """`manage.py scheduler --once` runs one pass and enqueues ready work."""
    _scenario()

    call_command("scheduler", "--once")

    # One ready task was dispatched and the run was started.
    assert OutboxMessage.objects.count() == 1
    assert WorkflowRun.objects.filter(state=WorkflowRunState.RUNNING.value).count() == 1
