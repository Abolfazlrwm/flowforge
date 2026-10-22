"""Saga-style compensation: on failure, undo the succeeded work in reverse order.

A compensation task (marked ``compensate_for``) is held out of the forward DAG —
it is not materialised when the run starts and is never promoted during normal
scheduling. When the run fails, the scheduler runs the compensations of the tasks
that had *succeeded*, strictly one at a time in reverse dependency order, and only
then lets the run finish as FAILED. These run on SQLite; the ordering is pure row
logic, no broker involved.
"""

from __future__ import annotations

import pytest
from core.domain.errors import DefinitionError
from core.domain.schema import parse_workflow_definition
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun, WorkflowRun
from core.repositories.definitions import publish_definition
from core.repositories.scheduler import advance_workflow_run
from core.services.runs import trigger

pytestmark = pytest.mark.django_db


def _saga_run(tasks: list[dict[str, object]]) -> tuple[WorkflowRun, dict[str, str]]:
    """Publish a saga definition, trigger a run, and mark it RUNNING.

    The run is set RUNNING because every scenario here is a run that has already
    started doing forward work and is now failing — which is the only state in
    which compensation applies.
    """
    workflow, _created = publish_definition(
        parse_workflow_definition({"name": "saga", "version": 1, "tasks": tasks})
    )
    run, _ = trigger(workflow, requested_by_id=None)
    WorkflowRun.objects.filter(pk=run.id).update(state=WorkflowRunState.RUNNING.value)
    return run, {}


def _set(run: WorkflowRun, **states: TaskRunState) -> None:
    """Force forward/compensation task runs into the given states."""
    for key, state in states.items():
        TaskRun.objects.filter(workflow_run=run, task__key=key).update(state=state.value)


def _state(run: WorkflowRun, key: str) -> str:
    """The current state of a task run, or ``""`` if it has no task run yet."""
    task_run = TaskRun.objects.filter(workflow_run=run, task__key=key).first()
    return task_run.state if task_run is not None else ""


def _run_state(run: WorkflowRun) -> str:
    run.refresh_from_db()
    return run.state


def test_a_compensation_task_is_not_materialised_in_the_forward_run() -> None:
    """The compensation is dormant until the run fails; it gets no task run yet."""
    run, _ = _saga_run(
        [
            {"key": "reserve", "task_type": "noop"},
            {"key": "charge", "task_type": "noop", "depends_on": ["reserve"]},
            {"key": "undo_reserve", "task_type": "noop", "compensate_for": "reserve"},
        ]
    )

    keys = set(TaskRun.objects.filter(workflow_run=run).values_list("task__key", flat=True))
    assert keys == {"reserve", "charge"}


def test_a_failed_run_compensates_the_succeeded_task() -> None:
    """Reserve succeeded and charge failed, so reserve is undone before FAILED."""
    run, _ = _saga_run(
        [
            {"key": "reserve", "task_type": "noop"},
            {"key": "charge", "task_type": "noop", "depends_on": ["reserve"]},
            {"key": "undo_reserve", "task_type": "noop", "compensate_for": "reserve"},
        ]
    )
    _set(run, reserve=TaskRunState.SUCCEEDED, charge=TaskRunState.FAILED)

    advance_workflow_run(run)

    # The compensation is triggered (READY) and the run is held RUNNING until it
    # settles — a saga does not report FAILED while it is still rolling back.
    assert _state(run, "undo_reserve") == TaskRunState.READY.value
    assert _run_state(run) == WorkflowRunState.RUNNING.value

    _set(run, undo_reserve=TaskRunState.SUCCEEDED)
    advance_workflow_run(run)
    assert _run_state(run) == WorkflowRunState.FAILED.value


def test_compensations_run_in_reverse_dependency_order() -> None:
    """A -> b -> c with c failing: undo b before undo a, one at a time."""
    run, _ = _saga_run(
        [
            {"key": "a", "task_type": "noop"},
            {"key": "b", "task_type": "noop", "depends_on": ["a"]},
            {"key": "c", "task_type": "noop", "depends_on": ["b"]},
            {"key": "undo_a", "task_type": "noop", "compensate_for": "a"},
            {"key": "undo_b", "task_type": "noop", "compensate_for": "b"},
        ]
    )
    _set(run, a=TaskRunState.SUCCEEDED, b=TaskRunState.SUCCEEDED, c=TaskRunState.FAILED)

    advance_workflow_run(run)
    # The dependent (b) is undone first, and only it — the sequence is strictly one
    # at a time, so undo_a has not even been created yet.
    assert _state(run, "undo_b") == TaskRunState.READY.value
    assert _state(run, "undo_a") == ""

    _set(run, undo_b=TaskRunState.SUCCEEDED)
    advance_workflow_run(run)
    assert _state(run, "undo_a") == TaskRunState.READY.value

    _set(run, undo_a=TaskRunState.SUCCEEDED)
    advance_workflow_run(run)
    assert _run_state(run) == WorkflowRunState.FAILED.value


def test_only_succeeded_tasks_are_compensated() -> None:
    """A task that failed (not succeeded) is not compensated, even if it can be."""
    run, _ = _saga_run(
        [
            {"key": "a", "task_type": "noop"},
            {"key": "b", "task_type": "noop", "depends_on": ["a"]},
            {"key": "undo_a", "task_type": "noop", "compensate_for": "a"},
            {"key": "undo_b", "task_type": "noop", "compensate_for": "b"},
        ]
    )
    # a succeeded, b failed: only a is compensated.
    _set(run, a=TaskRunState.SUCCEEDED, b=TaskRunState.FAILED)

    advance_workflow_run(run)

    assert _state(run, "undo_a") == TaskRunState.READY.value
    assert _state(run, "undo_b") == ""  # b failed, so it is not undone


def test_a_run_with_no_compensations_fails_immediately() -> None:
    """A plain failure with nothing to undo finalises FAILED in one pass."""
    run, _ = _saga_run(
        [
            {"key": "a", "task_type": "noop"},
            {"key": "b", "task_type": "noop", "depends_on": ["a"]},
        ]
    )
    _set(run, a=TaskRunState.SUCCEEDED, b=TaskRunState.FAILED)

    advance_workflow_run(run)

    assert _run_state(run) == WorkflowRunState.FAILED.value


def test_a_failed_compensation_halts_the_rollback() -> None:
    """If an undo fails, the saga stops rather than undoing later work on top."""
    run, _ = _saga_run(
        [
            {"key": "a", "task_type": "noop"},
            {"key": "b", "task_type": "noop", "depends_on": ["a"]},
            {"key": "c", "task_type": "noop", "depends_on": ["b"]},
            {"key": "undo_a", "task_type": "noop", "compensate_for": "a"},
            {"key": "undo_b", "task_type": "noop", "compensate_for": "b"},
        ]
    )
    _set(run, a=TaskRunState.SUCCEEDED, b=TaskRunState.SUCCEEDED, c=TaskRunState.FAILED)

    advance_workflow_run(run)  # reverse order: undo_b starts first
    assert _state(run, "undo_b") == TaskRunState.READY.value

    _set(run, undo_b=TaskRunState.DEAD)  # the undo itself failed hard
    advance_workflow_run(run)

    # undo_a is never started, and the run settles FAILED with the rollback halted.
    assert _state(run, "undo_a") == ""
    assert _run_state(run) == WorkflowRunState.FAILED.value


# -- definition validation -------------------------------------------------


def test_a_compensation_must_name_an_existing_task() -> None:
    with pytest.raises(DefinitionError):
        parse_workflow_definition(
            {
                "name": "s",
                "version": 1,
                "tasks": [
                    {"key": "a", "task_type": "noop"},
                    {"key": "undo", "task_type": "noop", "compensate_for": "missing"},
                ],
            }
        )


def test_a_task_cannot_compensate_itself() -> None:
    with pytest.raises(DefinitionError):
        parse_workflow_definition(
            {
                "name": "s",
                "version": 1,
                "tasks": [{"key": "a", "task_type": "noop", "compensate_for": "a"}],
            }
        )


def test_a_compensation_cannot_declare_dependencies() -> None:
    """A compensation runs outside the forward DAG, so depends_on is meaningless."""
    with pytest.raises(DefinitionError):
        parse_workflow_definition(
            {
                "name": "s",
                "version": 1,
                "tasks": [
                    {"key": "a", "task_type": "noop"},
                    {
                        "key": "undo",
                        "task_type": "noop",
                        "depends_on": ["a"],
                        "compensate_for": "a",
                    },
                ],
            }
        )
