"""Manual re-run of a FAILED task run.

The automatic retry lifecycle (`schedule_retries`) already moves a FAILED task to
RETRYING or DEAD. `requeue_failed_task_run` is the operator's override: reset the
task to a fresh PENDING attempt now. These run on SQLite — it is a conditional row
reset, not a concurrency primitive.
"""

from __future__ import annotations

import uuid

import pytest
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import OutboxMessage, Task, TaskRun, TaskRunEvent, Workflow, WorkflowRun
from core.repositories.scheduler import advance_workflow_run
from core.repositories.transitions import requeue_failed_task_run

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


def _failed_run() -> tuple[WorkflowRun, TaskRun]:
    """A one-task run whose task FAILED and whose run therefore FAILED."""
    workflow = Workflow.objects.create(name="etl", version=1)
    task = Task.objects.create(workflow=workflow, key="extract", task_type="extract")
    run = WorkflowRun.objects.create(
        workflow=workflow, state=WorkflowRunState.FAILED.value, finished_at=NOW
    )
    task_run = TaskRun.objects.create(
        workflow_run=run,
        task=task,
        state=TaskRunState.FAILED.value,
        attempt=3,
        finished_at=NOW,
    )
    return run, task_run


def test_a_failed_task_is_reset_to_a_fresh_pending_attempt() -> None:
    """FAILED -> PENDING, attempt back to 1, token bumped, failed run reverted."""
    run, task_run = _failed_run()
    assert task_run.fencing_token == 0

    assert requeue_failed_task_run(task_run_id=task_run.id, now=NOW) is True

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.PENDING.value
    assert task_run.attempt == 1
    assert task_run.finished_at is None
    assert task_run.fencing_token == 1
    run.refresh_from_db()
    assert run.state == WorkflowRunState.PENDING.value
    assert run.finished_at is None
    assert TaskRunEvent.objects.get().kind == "requeue"


def test_a_non_failed_task_is_left_alone() -> None:
    """Only a FAILED task run is a candidate; anything else is a no-op."""
    _run, task_run = _failed_run()
    TaskRun.objects.filter(pk=task_run.id).update(state=TaskRunState.SUCCEEDED.value)

    assert requeue_failed_task_run(task_run_id=task_run.id, now=NOW) is False
    assert TaskRunEvent.objects.count() == 0


def test_a_finished_run_is_not_resurrected() -> None:
    """Re-running one task must not drag a SUCCEEDED/CANCELLED run back to life."""
    run, task_run = _failed_run()
    WorkflowRun.objects.filter(pk=run.id).update(state=WorkflowRunState.SUCCEEDED.value)

    assert requeue_failed_task_run(task_run_id=task_run.id, now=NOW) is True

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.PENDING.value  # the task is requeued
    run.refresh_from_db()
    assert run.state == WorkflowRunState.SUCCEEDED.value  # but the run stays finished


def test_a_missing_task_run_is_a_noop() -> None:
    """An unknown id is False, not an error."""
    assert requeue_failed_task_run(task_run_id=uuid.uuid4(), now=NOW) is False


def test_a_requeued_task_is_re_dispatched_on_the_next_pass() -> None:
    """After a manual re-run the scheduler promotes and dispatches the task again."""
    run, task_run = _failed_run()
    requeue_failed_task_run(task_run_id=task_run.id, now=NOW)

    _promoted, enqueued = advance_workflow_run(run)

    assert enqueued == 1
    assert OutboxMessage.objects.count() == 1
    run.refresh_from_db()
    assert run.state == WorkflowRunState.RUNNING.value
