"""Property-based tests.

The example-based tests in ``test_state_machine.py`` check the pairs a human
thought of. These check the pairs nobody thought of: Hypothesis drives random
sequences of attempted transitions through the real table and asserts the
invariants hold at every step.

The stateful test is the important one. It models a caller that keeps trying
transitions — legal and illegal, in arbitrary order — which is exactly what a
buggy worker or a racing client does.
"""

from __future__ import annotations

import pytest
from core.domain.errors import IllegalTransitionError, InvariantViolationError
from core.domain.machines import TASK_RUN_MACHINE, WORKFLOW_RUN_MACHINE
from core.domain.services import derive_workflow_run_state, initial_task_run_state
from core.domain.states import TaskRunState, WorkflowRunState
from core.domain.transitions import StateMachine
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

MACHINES = (WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE)


@given(st.sampled_from(MACHINES), st.data())
@settings(max_examples=200, deadline=None)
def test_every_declared_edge_applies(
    machine: StateMachine[WorkflowRunState] | StateMachine[TaskRunState],
    data: st.DataObject,
) -> None:
    """Any edge in the table must be applicable, and return its target."""
    edge = data.draw(st.sampled_from(list(machine.transitions)))
    result = machine.apply(edge.source, edge.target, lease_held=edge.requires_lease)
    assert result is edge.target


@given(st.sampled_from(MACHINES), st.data())
@settings(max_examples=200, deadline=None)
def test_any_undeclared_pair_is_rejected(
    machine: StateMachine[WorkflowRunState] | StateMachine[TaskRunState],
    data: st.DataObject,
) -> None:
    """Any pair not in the table must raise, whichever states are chosen."""
    source = data.draw(st.sampled_from(list(machine.states)))
    target = data.draw(st.sampled_from(list(machine.states)))
    if machine.can(source, target):
        return
    with pytest.raises(IllegalTransitionError):
        machine.apply(source, target, lease_held=True)


class _TransitionWalker(RuleBasedStateMachine):
    """Attempt random transitions, legal or not, and check the invariants.

    The model is intentionally thin: it asserts what must be true of *any*
    correct machine rather than reimplementing the table, which would just
    compare the table to itself.
    """

    def __init__(self) -> None:
        super().__init__()
        self.machine = TASK_RUN_MACHINE
        self.current: TaskRunState = TaskRunState.PENDING
        self.visited: set[TaskRunState] = {self.current}

    @rule(target_state=st.sampled_from(list(TaskRunState)))
    def attempt(self, target_state: TaskRunState) -> None:
        """Try to move to ``target_state``; assert the outcome matches the table."""
        target = target_state
        before = self.current
        edge = next(
            (e for e in self.machine.outgoing(before) if e.target is target),
            None,
        )
        if edge is None:
            with pytest.raises(IllegalTransitionError):
                self.machine.apply(before, target, lease_held=True)
            # A rejected transition must not move the caller.
            assert self.current is before
            return

        result = self.machine.apply(before, target, lease_held=edge.requires_lease)
        assert result is target
        self.current = result
        self.visited.add(result)

    @invariant()
    def state_is_always_a_member(self) -> None:
        """The walker can only ever be in a declared state."""
        assert isinstance(self.current, TaskRunState)
        assert self.current in set(TaskRunState)

    @invariant()
    def terminal_states_absorb(self) -> None:
        """Once terminal, there is nowhere to go."""
        if self.machine.is_terminal(self.current):
            assert self.machine.targets(self.current) == frozenset()

    @invariant()
    def reached_states_are_declared(self) -> None:
        """Every visited state is reachable and declared."""
        assert self.visited <= set(TaskRunState)


def test_transition_walker() -> None:
    """Drive the stateful test with Hypothesis' own shrinking."""
    run_state_machine_as_test(
        _TransitionWalker,
        settings=settings(
            max_examples=60,
            stateful_step_count=25,
            deadline=None,
            suppress_health_check=[HealthCheck.too_slow],
        ),
    )


@given(states=st.lists(st.sampled_from(list(TaskRunState)), max_size=8))
@settings(max_examples=300, deadline=None)
def test_derive_never_reports_pending_once_work_started(
    states: list[TaskRunState],
) -> None:
    """``PENDING`` must mean 'nothing has happened yet', nothing else."""
    result = derive_workflow_run_state(states)
    if any(state is not TaskRunState.PENDING for state in states):
        assert result is not WorkflowRunState.PENDING


@given(states=st.lists(st.sampled_from(list(TaskRunState)), min_size=1, max_size=8))
@settings(max_examples=300, deadline=None)
def test_derive_prefers_cancelled_over_everything(states: list[TaskRunState]) -> None:
    """A cancellation is an explicit instruction and outranks other outcomes."""
    assert derive_workflow_run_state([*states, TaskRunState.CANCELLED]) is (
        WorkflowRunState.CANCELLED
    )


@given(states=st.lists(st.sampled_from(list(TaskRunState)), min_size=1, max_size=8))
@settings(max_examples=300, deadline=None)
def test_derive_prefers_failed_over_success(states: list[TaskRunState]) -> None:
    """A failure must surface even while other tasks are still running.

    Without this, an operator sees ``RUNNING`` and learns about the failure only
    when the slowest task finishes.
    """
    result = derive_workflow_run_state([*states, TaskRunState.FAILED])
    if TaskRunState.CANCELLED not in states:
        assert result is WorkflowRunState.FAILED


@given(states=st.lists(st.just(TaskRunState.SUCCEEDED), min_size=1, max_size=8))
@settings(max_examples=50, deadline=None)
def test_derive_reports_succeeded_only_when_everything_succeeded(
    states: list[TaskRunState],
) -> None:
    """All-success is the only path to ``SUCCEEDED``."""
    assert derive_workflow_run_state(states) is WorkflowRunState.SUCCEEDED


@given(dependency_count=st.integers(min_value=0, max_value=10_000))
@settings(max_examples=100, deadline=None)
def test_initial_state_depends_only_on_whether_dependencies_exist(
    dependency_count: int,
) -> None:
    """The rule is binary, so the count must not leak into the result."""
    expected = TaskRunState.READY if dependency_count == 0 else TaskRunState.PENDING
    assert initial_task_run_state(dependency_count=dependency_count) is expected


@given(dependency_count=st.integers(max_value=-1))
@settings(max_examples=50, deadline=None)
def test_negative_dependency_count_is_refused(dependency_count: int) -> None:
    """A negative count is meaningless and must not silently behave like zero."""
    with pytest.raises(InvariantViolationError):
        initial_task_run_state(dependency_count=dependency_count)


def test_derive_rejects_a_non_state_value() -> None:
    """Passing a raw string would compare unequal forever and pick a wrong branch."""
    with pytest.raises(InvariantViolationError):
        derive_workflow_run_state(["succeeded"])  # type: ignore[list-item]


def test_derive_of_nothing_is_pending() -> None:
    """A run with no tasks has not started."""
    assert derive_workflow_run_state([]) is WorkflowRunState.PENDING
