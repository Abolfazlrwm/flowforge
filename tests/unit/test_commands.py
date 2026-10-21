"""Management commands that are not a long-running loop.

The scheduler, publisher and worker loops are exercised elsewhere (the scheduler
by ``test_scheduler.py``, the worker against real RabbitMQ in the integration
suite). This file covers the operator-facing one-shots, where the exit code is
part of the contract.
"""

from __future__ import annotations

import uuid

import pytest
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun, WorkflowRun
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


def _dead_task_run(task_run: TaskRun) -> TaskRun:
    """Put ``task_run`` into the terminal DEAD state, as a buried retry would."""
    TaskRun.objects.filter(pk=task_run.pk).update(
        state=TaskRunState.DEAD.value,
        attempt=3,
        finished_at=NOW,
        result={"stale": True},
    )
    WorkflowRun.objects.filter(pk=task_run.workflow_run_id).update(
        state=WorkflowRunState.FAILED.value,
        started_at=NOW,
        finished_at=NOW,
    )
    task_run.refresh_from_db()
    return task_run


def test_requeue_dead_resets_the_task_and_its_run(task_run: TaskRun) -> None:
    """The operator's recovery path must leave the task dispatchable again."""
    _dead_task_run(task_run)

    call_command("requeue_dead", str(task_run.id))

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.PENDING.value
    assert task_run.attempt == 1
    assert task_run.result is None
    assert task_run.finished_at is None

    run = WorkflowRun.objects.get(pk=task_run.workflow_run_id)
    assert run.state == WorkflowRunState.PENDING.value


def test_requeue_dead_refuses_a_task_that_is_not_dead(task_run: TaskRun) -> None:
    """A READY task is already dispatchable; requeueing it would duplicate work."""
    with pytest.raises(CommandError, match="not DEAD"):
        call_command("requeue_dead", str(task_run.id))

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.READY.value


def test_requeue_dead_rejects_a_malformed_id() -> None:
    """A typo must fail loudly, not silently requeue nothing."""
    with pytest.raises(CommandError, match="not a valid UUID"):
        call_command("requeue_dead", "not-a-uuid")


def test_requeue_dead_reports_an_unknown_id() -> None:
    """An id that matches no row is an error, not a no-op success."""
    with pytest.raises(CommandError, match="does not exist"):
        call_command("requeue_dead", str(uuid.uuid4()))
