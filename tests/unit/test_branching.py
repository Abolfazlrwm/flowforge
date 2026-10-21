"""Conditional branching and fan-out/fan-in.

The interesting part is not "a false condition skips the task" — it is what
happens *around* it: a skipped branch must not leave its dependants pending
forever, a fan-in must not fire early, and a broken condition must fail loudly
instead of quietly skipping work the author expected to run.
"""

from __future__ import annotations

import pytest
from core.domain.expressions import ExpressionError
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import Task, TaskRun, Workflow, WorkflowRun
from core.repositories.scheduler import advance_workflow_run

pytestmark = pytest.mark.django_db


def _build(
    tasks: list[dict[str, object]],
    *,
    results: dict[str, object] | None = None,
    states: dict[str, TaskRunState] | None = None,
) -> tuple[WorkflowRun, dict[str, TaskRun]]:
    """Materialise a run from a compact task list.

    ``states`` overrides a task run's starting state; ``results`` seeds the
    persisted handler output that conditions read.
    """
    workflow = Workflow.objects.create(name="branch", version=1)
    run = WorkflowRun.objects.create(workflow=workflow, state=WorkflowRunState.PENDING.value)
    task_runs: dict[str, TaskRun] = {}

    for spec in tasks:
        key = str(spec["key"])
        task = Task.objects.create(
            workflow=workflow,
            key=key,
            task_type=str(spec.get("task_type", "noop")),
            depends_on=list(spec.get("depends_on", [])),  # type: ignore[arg-type]
            config=dict(spec.get("config", {})),  # type: ignore[arg-type]
            when=spec.get("when"),  # type: ignore[arg-type]
            join=str(spec.get("join", "all")),
        )
        state = (states or {}).get(key, TaskRunState.PENDING)
        task_runs[key] = TaskRun.objects.create(
            workflow_run=run,
            task=task,
            state=state.value,
            result=(results or {}).get(key),
        )
    return run, task_runs


def _state(task_runs: dict[str, TaskRun], key: str) -> str:
    task_runs[key].refresh_from_db()
    return task_runs[key].state


def test_a_false_condition_skips_the_task() -> None:
    """The branch is not taken: the task is skipped, not failed and not run."""
    run, runs = _build(
        [
            {"key": "guard"},
            {"key": "charge", "depends_on": ["guard"], "when": "tasks.guard.amount > 100"},
        ],
        states={"guard": TaskRunState.SUCCEEDED},
        results={"guard": {"amount": 50}},
    )

    advance_workflow_run(run)

    assert _state(runs, "charge") == TaskRunState.SKIPPED.value
    run.refresh_from_db()
    assert run.state == WorkflowRunState.SUCCEEDED.value


def test_a_true_condition_promotes_the_task() -> None:
    """The same definition runs when the condition holds."""
    run, runs = _build(
        [
            {"key": "guard"},
            {"key": "charge", "depends_on": ["guard"], "when": "tasks.guard.amount > 100"},
        ],
        states={"guard": TaskRunState.SUCCEEDED},
        results={"guard": {"amount": 500}},
    )

    advance_workflow_run(run)

    assert _state(runs, "charge") == TaskRunState.READY.value


def test_a_skipped_task_skips_its_dependants() -> None:
    """A branch that was not taken cannot feed the tasks below it.

    Without propagation the dependant stays ``PENDING`` forever and the run never
    completes — a stall is worse than a skip, because nothing reports it.
    """
    run, runs = _build(
        [
            {"key": "guard"},
            {"key": "charge", "depends_on": ["guard"], "when": "false"},
            {"key": "notify", "depends_on": ["charge"]},
        ],
        states={"guard": TaskRunState.SUCCEEDED},
    )

    advance_workflow_run(run)

    assert _state(runs, "charge") == TaskRunState.SKIPPED.value
    assert _state(runs, "notify") == TaskRunState.SKIPPED.value
    run.refresh_from_db()
    assert run.state == WorkflowRunState.SUCCEEDED.value


def test_a_run_whose_only_task_is_skipped_still_completes() -> None:
    """A skipped task is *resolved*, so the run is not left pending."""
    run, runs = _build([{"key": "optional", "when": "false"}])

    advance_workflow_run(run)

    assert _state(runs, "optional") == TaskRunState.SKIPPED.value
    run.refresh_from_db()
    assert run.state == WorkflowRunState.SUCCEEDED.value


def test_a_condition_that_names_an_unknown_task_fails_loudly() -> None:
    """A typo must not silently skip the work the author expected to run."""
    run, _runs = _build(
        [{"key": "charge", "when": "tasks.missing.amount > 100"}],
    )

    with pytest.raises(ExpressionError, match=r"tasks\.missing"):
        advance_workflow_run(run)


class TestFanOutFanIn:
    """One task feeding many, and many feeding one."""

    def test_fan_out_promotes_every_dependant_at_once(self) -> None:
        """A completed task unlocks all of its children in one pass."""
        run, runs = _build(
            [
                {"key": "root"},
                {"key": "a", "depends_on": ["root"]},
                {"key": "b", "depends_on": ["root"]},
                {"key": "c", "depends_on": ["root"]},
            ],
            states={"root": TaskRunState.SUCCEEDED},
        )

        advance_workflow_run(run)

        assert _state(runs, "a") == TaskRunState.READY.value
        assert _state(runs, "b") == TaskRunState.READY.value
        assert _state(runs, "c") == TaskRunState.READY.value

    def test_fan_in_waits_for_every_dependency_by_default(self) -> None:
        """`join="all"` must not fire on a partial result."""
        run, runs = _build(
            [
                {"key": "a"},
                {"key": "b"},
                {"key": "join", "depends_on": ["a", "b"]},
            ],
            states={"a": TaskRunState.SUCCEEDED, "b": TaskRunState.PENDING},
        )

        advance_workflow_run(run)
        assert _state(runs, "join") == TaskRunState.PENDING.value

        TaskRun.objects.filter(pk=runs["b"].pk).update(state=TaskRunState.SUCCEEDED.value)
        advance_workflow_run(run)
        assert _state(runs, "join") == TaskRunState.READY.value

    def test_join_any_runs_when_one_branch_succeeds(self) -> None:
        """Alternative branches: whichever path completed is enough."""
        run, runs = _build(
            [
                {"key": "a"},
                {"key": "b"},
                {"key": "join", "depends_on": ["a", "b"], "join": "any"},
            ],
            states={"a": TaskRunState.SUCCEEDED, "b": TaskRunState.SKIPPED},
        )

        advance_workflow_run(run)

        assert _state(runs, "join") == TaskRunState.READY.value

    def test_join_any_is_skipped_when_every_branch_is_skipped(self) -> None:
        """No path survived, so the join cannot run either."""
        run, runs = _build(
            [
                {"key": "a"},
                {"key": "b"},
                {"key": "join", "depends_on": ["a", "b"], "join": "any"},
            ],
            states={"a": TaskRunState.SKIPPED, "b": TaskRunState.SKIPPED},
        )

        advance_workflow_run(run)

        assert _state(runs, "join") == TaskRunState.SKIPPED.value

    def test_join_any_still_waits_while_a_branch_is_in_flight(self) -> None:
        """One skipped branch does not decide the join while another may still win."""
        run, runs = _build(
            [
                {"key": "a"},
                {"key": "b"},
                {"key": "join", "depends_on": ["a", "b"], "join": "any"},
            ],
            states={"a": TaskRunState.SKIPPED, "b": TaskRunState.PENDING},
        )

        advance_workflow_run(run)

        assert _state(runs, "join") == TaskRunState.PENDING.value
