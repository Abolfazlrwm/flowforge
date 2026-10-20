"""The transition tables and the machine that reads them.

These tests are deliberately exhaustive over the state space rather than
spot-checking a few edges: the table is small, so there is no reason to sample
it. Every (source, target) pair is either declared or it is not, and both
outcomes are asserted.
"""

from __future__ import annotations

import pytest
from core.domain.errors import IllegalTransitionError, LeaseRequiredError
from core.domain.machines import TASK_RUN_MACHINE, WORKFLOW_RUN_MACHINE
from core.domain.states import TaskRunState, WorkflowRunState
from core.domain.transitions import StateMachine, Transition


def test_workflow_run_table_is_exactly_as_documented() -> None:
    """Pin the table. A change here should be a deliberate, reviewed change."""
    assert WORKFLOW_RUN_MACHINE.rows() == (
        ("start", "pending", "running", False),
        ("cancel_before_start", "pending", "cancelled", False),
        ("succeed", "running", "succeeded", False),
        ("fail", "running", "failed", False),
        ("cancel", "running", "cancelled", False),
    )


def test_task_run_table_is_exactly_as_documented() -> None:
    """Pin the table, including which edges require a held lease."""
    assert TASK_RUN_MACHINE.rows() == (
        ("dependencies_satisfied", "pending", "ready", False),
        ("cancel_pending", "pending", "cancelled", False),
        ("claim", "ready", "running", False),
        ("cancel_ready", "ready", "cancelled", False),
        ("succeed", "running", "succeeded", True),
        ("fail", "running", "failed", True),
        ("cancel_running", "running", "cancelled", True),
        ("reap_lease", "running", "ready", False),
        ("schedule_retry", "failed", "retrying", False),
        ("retry_ready", "retrying", "ready", False),
    )


@pytest.mark.parametrize("machine", [WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE])
def test_terminal_states_have_no_outgoing_transitions(
    machine: StateMachine[WorkflowRunState] | StateMachine[TaskRunState],
) -> None:
    """A terminal state must be absorbing, or the word means nothing."""
    for state in machine.terminal:
        assert machine.outgoing(state) == ()
        assert machine.targets(state) == frozenset()


@pytest.mark.parametrize("machine", [WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE])
def test_every_non_terminal_state_can_leave(
    machine: StateMachine[WorkflowRunState] | StateMachine[TaskRunState],
) -> None:
    """No dead ends: every non-terminal state must have a way out."""
    for state in machine.states:
        if state in machine.terminal:
            continue
        assert machine.targets(state), f"{state!r} has no outgoing transition"


@pytest.mark.parametrize("machine", [WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE])
def test_every_state_is_reachable_from_the_initial_state(
    machine: StateMachine[WorkflowRunState] | StateMachine[TaskRunState],
) -> None:
    """An unreachable state is dead code pretending to be a state."""
    initial = next(iter(machine.states))
    seen = {initial}
    frontier = [initial]
    while frontier:
        current = frontier.pop()
        for target in machine.targets(current):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == set(machine.states)


@pytest.mark.parametrize("machine", [WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE])
def test_no_self_loops(
    machine: StateMachine[WorkflowRunState] | StateMachine[TaskRunState],
) -> None:
    """A self-loop would be a transition that changes nothing."""
    for edge in machine:
        assert edge.source is not edge.target


def test_can_agrees_with_targets_for_every_pair() -> None:
    """``can`` is a convenience over ``targets``; they must not disagree."""
    for machine in (WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE):
        for source in machine.states:
            reachable = machine.targets(source)
            for target in machine.states:
                assert machine.can(source, target) == (target in reachable)


def test_sources_is_the_inverse_of_targets() -> None:
    """Every declared edge must appear in both directions of the relation."""
    for machine in (WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE):
        for edge in machine:
            assert edge.source in machine.sources(edge.target)
            assert edge.target in machine.targets(edge.source)


@pytest.mark.parametrize(
    ("machine", "source", "target"),
    [
        (WORKFLOW_RUN_MACHINE, WorkflowRunState.SUCCEEDED, WorkflowRunState.RUNNING),
        (WORKFLOW_RUN_MACHINE, WorkflowRunState.PENDING, WorkflowRunState.SUCCEEDED),
        (WORKFLOW_RUN_MACHINE, WorkflowRunState.RUNNING, WorkflowRunState.PENDING),
        (WORKFLOW_RUN_MACHINE, WorkflowRunState.CANCELLED, WorkflowRunState.PENDING),
        (TASK_RUN_MACHINE, TaskRunState.PENDING, TaskRunState.RUNNING),
        (TASK_RUN_MACHINE, TaskRunState.READY, TaskRunState.SUCCEEDED),
        (TASK_RUN_MACHINE, TaskRunState.SUCCEEDED, TaskRunState.READY),
        (TASK_RUN_MACHINE, TaskRunState.RETRYING, TaskRunState.RUNNING),
        (TASK_RUN_MACHINE, TaskRunState.FAILED, TaskRunState.READY),
    ],
)
def test_illegal_transitions_raise_and_name_the_legal_alternatives(
    machine: StateMachine[WorkflowRunState] | StateMachine[TaskRunState],
    source: WorkflowRunState | TaskRunState,
    target: WorkflowRunState | TaskRunState,
) -> None:
    """An illegal transition must explain itself, not just say no."""
    with pytest.raises(IllegalTransitionError) as excinfo:
        machine.apply(source, target)

    error = excinfo.value
    assert error.source == source.value
    assert error.target == target.value
    assert error.allowed == tuple(sorted(t.value for t in machine.targets(source)))
    assert source.value in str(error)
    assert target.value in str(error)


def test_error_for_a_terminal_state_says_so() -> None:
    """The message should distinguish 'not allowed' from 'nothing is allowed'."""
    with pytest.raises(IllegalTransitionError) as excinfo:
        WORKFLOW_RUN_MACHINE.apply(WorkflowRunState.FAILED, WorkflowRunState.RUNNING)
    assert "terminal" in str(excinfo.value)


def test_lease_is_required_to_finish_a_running_task() -> None:
    """Only the lease holder may write an outcome."""
    for target in (TaskRunState.SUCCEEDED, TaskRunState.FAILED, TaskRunState.CANCELLED):
        with pytest.raises(LeaseRequiredError):
            TASK_RUN_MACHINE.apply(TaskRunState.RUNNING, target)


def test_lease_is_not_required_to_claim_a_task() -> None:
    """Claiming is what *acquires* the lease, so it cannot require one."""
    assert TASK_RUN_MACHINE.apply(TaskRunState.READY, TaskRunState.RUNNING) is (
        TaskRunState.RUNNING
    )


def test_apply_returns_the_target_when_the_edge_is_legal() -> None:
    """``apply`` is the validated form of 'this is where I am going'."""
    assert (
        WORKFLOW_RUN_MACHINE.apply(WorkflowRunState.PENDING, WorkflowRunState.RUNNING)
        is WorkflowRunState.RUNNING
    )


class TestTableValidation:
    """A malformed table must fail at construction, not at request time."""

    def test_rejects_duplicate_edge(self) -> None:
        """Two edges between the same pair make the outcome ambiguous."""
        with pytest.raises(ValueError, match="duplicate edge"):
            StateMachine(
                name="X",
                states=WorkflowRunState,
                terminal=(WorkflowRunState.CANCELLED,),
                transitions=(
                    Transition("a", WorkflowRunState.PENDING, WorkflowRunState.RUNNING),
                    Transition("b", WorkflowRunState.PENDING, WorkflowRunState.RUNNING),
                ),
            )

    def test_rejects_duplicate_transition_name(self) -> None:
        """Names identify edges in logs and docs, so they must be unique."""
        with pytest.raises(ValueError, match="duplicate transition name"):
            StateMachine(
                name="X",
                states=WorkflowRunState,
                terminal=(WorkflowRunState.CANCELLED,),
                transitions=(
                    Transition("same", WorkflowRunState.PENDING, WorkflowRunState.RUNNING),
                    Transition("same", WorkflowRunState.RUNNING, WorkflowRunState.CANCELLED),
                ),
            )

    def test_rejects_edges_out_of_a_terminal_state(self) -> None:
        """A terminal state with an exit is not terminal."""
        with pytest.raises(ValueError, match="declared terminal"):
            StateMachine(
                name="X",
                states=WorkflowRunState,
                # A complete table, except that SUCCEEDED both is terminal and
                # has an exit. Without the rest of the table the dead-end check
                # would fire first and the test would pass for the wrong reason.
                terminal=(
                    WorkflowRunState.SUCCEEDED,
                    WorkflowRunState.FAILED,
                    WorkflowRunState.CANCELLED,
                ),
                transitions=(
                    Transition("start", WorkflowRunState.PENDING, WorkflowRunState.RUNNING),
                    Transition("succeed", WorkflowRunState.RUNNING, WorkflowRunState.SUCCEEDED),
                    Transition("resume", WorkflowRunState.SUCCEEDED, WorkflowRunState.RUNNING),
                ),
            )

    def test_rejects_a_dead_end(self) -> None:
        """A non-terminal state with no exit would hang a run forever."""
        with pytest.raises(ValueError, match="no outgoing transitions"):
            StateMachine(
                name="X",
                states=WorkflowRunState,
                terminal=(),
                transitions=(
                    Transition("go", WorkflowRunState.PENDING, WorkflowRunState.RUNNING),
                    Transition("back", WorkflowRunState.RUNNING, WorkflowRunState.PENDING),
                ),
            )

    def test_rejects_a_terminal_state_from_another_enum(self) -> None:
        """A cross-enum terminal must be refused even though it compares equal."""
        with pytest.raises(ValueError, match="is not a WorkflowRunState"):
            StateMachine(
                name="X",
                states=WorkflowRunState,
                terminal=(TaskRunState.CANCELLED,),  # type: ignore[arg-type]
                transitions=(
                    Transition("go", WorkflowRunState.PENDING, WorkflowRunState.RUNNING),
                    Transition("end", WorkflowRunState.RUNNING, WorkflowRunState.SUCCEEDED),
                ),
            )

    def test_rejects_an_edge_from_another_enum(self) -> None:
        """A source from the wrong enum is a type error the table must catch."""
        with pytest.raises(ValueError, match="not a WorkflowRunState"):
            StateMachine(
                name="X",
                states=WorkflowRunState,
                terminal=(WorkflowRunState.SUCCEEDED,),
                transitions=(
                    Transition(
                        "go",
                        TaskRunState.READY,  # type: ignore[arg-type]
                        WorkflowRunState.RUNNING,
                    ),
                ),
            )


def test_repr_shows_shape_not_the_whole_table() -> None:
    """The repr is for debugging, so it should stay one line."""
    text = repr(TASK_RUN_MACHINE)
    assert "TaskRun" in text
    assert "edges=10" in text
    assert "\n" not in text


class TestStrEnumEqualityTrap:
    """Documents a trap that cost a bug during Phase 1.

    Both state enums are ``StrEnum``, so two members from *different* enums with
    the same value compare equal and hash identically:

        TaskRunState.CANCELLED == WorkflowRunState.CANCELLED   # True

    That means ``set(terminal) - set(states)`` silently finds nothing, and a
    membership test cannot tell the enums apart. The machine therefore validates
    with ``isinstance``. These assertions exist so that if someone later
    "simplifies" that check back into a set operation, a test fails and explains
    why rather than the bug reappearing as a table that accepts the wrong enum.
    """

    def test_the_trap_is_real(self) -> None:
        """If this ever stops being true, the isinstance check can be relaxed."""
        assert TaskRunState.CANCELLED == WorkflowRunState.CANCELLED
        assert hash(TaskRunState.CANCELLED) == hash(WorkflowRunState.CANCELLED)
        assert TaskRunState.CANCELLED in {WorkflowRunState.CANCELLED}

    def test_the_machine_still_separates_them(self) -> None:
        """``isinstance`` is what actually distinguishes the two enums."""
        assert not isinstance(TaskRunState.CANCELLED, WorkflowRunState)
        assert isinstance(WorkflowRunState.CANCELLED, WorkflowRunState)

    def test_a_cross_enum_edge_source_is_refused(self) -> None:
        """A TaskRunState source must not be accepted by the workflow machine."""
        with pytest.raises(ValueError, match="not a WorkflowRunState"):
            StateMachine(
                name="X",
                states=WorkflowRunState,
                terminal=(WorkflowRunState.SUCCEEDED,),
                transitions=(
                    Transition(
                        "go",
                        TaskRunState.READY,  # type: ignore[arg-type]
                        WorkflowRunState.RUNNING,
                    ),
                ),
            )
