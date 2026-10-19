"""Entity invariants.

Each rule here is also a database CHECK constraint. Testing it twice is not
redundancy: the domain test proves the rule is stated, and the constraint test
(``test_constraints.py``) proves it is enforced against writers that never go
through this code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from core.domain.entities import Task, TaskRun, Workflow, WorkflowRun
from core.domain.errors import InvariantViolationError
from core.domain.states import TaskRunState, WorkflowRunState

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class TestWorkflow:
    """Identity rules for a definition."""

    def test_accepts_a_normal_definition(self) -> None:
        """The happy path should not need ceremony."""
        workflow = Workflow(name="etl")
        assert workflow.version == 1
        assert workflow.id is not None

    @pytest.mark.parametrize("name", ["", "   ", "\n\t"])
    def test_rejects_a_blank_name(self, name: str) -> None:
        """A blank name makes the workflow unaddressable."""
        with pytest.raises(InvariantViolationError):
            Workflow(name=name)

    @pytest.mark.parametrize("version", [0, -1])
    def test_rejects_a_non_positive_version(self, version: int) -> None:
        """Versions start at 1 so 'no version' is never representable."""
        with pytest.raises(InvariantViolationError):
            Workflow(name="etl", version=version)


class TestTask:
    """Identity rules for a step."""

    def test_accepts_a_normal_task(self) -> None:
        """The happy path."""
        task = Task(workflow_id=uuid4(), key="extract")
        assert task.key == "extract"

    def test_rejects_a_blank_key(self) -> None:
        """The key is how a run refers to the step, so it must exist."""
        with pytest.raises(InvariantViolationError):
            Task(workflow_id=uuid4(), key="  ")


class TestWorkflowRun:
    """Timestamp invariants tied to the run's state."""

    def test_a_pending_run_has_not_finished(self) -> None:
        """Defaults must satisfy the invariant, not violate it."""
        run = WorkflowRun(workflow_id=uuid4())
        assert run.state is WorkflowRunState.PENDING
        assert run.finished_at is None

    @pytest.mark.parametrize(
        "state",
        [WorkflowRunState.SUCCEEDED, WorkflowRunState.FAILED, WorkflowRunState.CANCELLED],
    )
    def test_a_terminal_run_must_have_finished_at(self, state: WorkflowRunState) -> None:
        """Otherwise every duration metric silently skips these rows."""
        with pytest.raises(InvariantViolationError, match="must have finished_at"):
            WorkflowRun(workflow_id=uuid4(), state=state)

    def test_a_terminal_run_with_finished_at_is_accepted(self) -> None:
        """The rule forbids missing timestamps, not terminal states."""
        run = WorkflowRun(
            workflow_id=uuid4(),
            state=WorkflowRunState.SUCCEEDED,
            started_at=T0,
            finished_at=T0 + timedelta(seconds=5),
        )
        assert run.state is WorkflowRunState.SUCCEEDED

    def test_a_non_terminal_run_must_not_have_finished_at(self) -> None:
        """A running run with a finish time looks done to anything sorting by it."""
        with pytest.raises(InvariantViolationError, match="must not have finished_at"):
            WorkflowRun(workflow_id=uuid4(), state=WorkflowRunState.RUNNING, finished_at=T0)

    def test_finished_at_cannot_precede_started_at(self) -> None:
        """Negative durations are a bug, not a rounding error."""
        with pytest.raises(InvariantViolationError, match="precede"):
            WorkflowRun(
                workflow_id=uuid4(),
                state=WorkflowRunState.SUCCEEDED,
                started_at=T0,
                finished_at=T0 - timedelta(seconds=1),
            )

    def test_state_is_coerced_from_its_string_value(self) -> None:
        """Rows loaded from the database arrive as strings."""
        run = WorkflowRun(workflow_id=uuid4(), state="pending")  # type: ignore[arg-type]
        assert run.state is WorkflowRunState.PENDING

    def test_an_unknown_state_value_is_rejected(self) -> None:
        """Coercion must fail loudly rather than invent a state."""
        with pytest.raises(ValueError, match="bogus"):
            WorkflowRun(workflow_id=uuid4(), state="bogus")  # type: ignore[arg-type]


class TestTaskRun:
    """The invariants that make leases and fencing meaningful."""

    def test_a_pending_task_run_holds_no_lease(self) -> None:
        """Defaults are valid."""
        run = TaskRun(workflow_run_id=uuid4(), task_id=uuid4())
        assert run.state is TaskRunState.PENDING
        assert run.lease_expires_at is None
        assert run.fencing_token == 0

    def test_a_running_task_run_must_hold_a_lease(self) -> None:
        """Without a lease, a crashed worker is indistinguishable from a slow one."""
        with pytest.raises(InvariantViolationError, match="must hold a lease"):
            TaskRun(
                workflow_run_id=uuid4(),
                task_id=uuid4(),
                state=TaskRunState.RUNNING,
            )

    def test_an_idle_task_run_must_not_hold_a_lease(self) -> None:
        """A lease on an idle row would let a stale owner keep it forever."""
        with pytest.raises(InvariantViolationError, match="only a running task run"):
            TaskRun(
                workflow_run_id=uuid4(),
                task_id=uuid4(),
                state=TaskRunState.READY,
                lease_owner="worker-1",
                lease_expires_at=T0,
            )

    def test_a_held_lease_must_name_its_owner(self) -> None:
        """An unattributed lease cannot be renewed or released deliberately."""
        with pytest.raises(InvariantViolationError, match="must name its owner"):
            TaskRun(
                workflow_run_id=uuid4(),
                task_id=uuid4(),
                state=TaskRunState.RUNNING,
                lease_expires_at=T0,
            )

    def test_a_valid_running_task_run_is_accepted(self) -> None:
        """The positive case for the lease invariant."""
        run = TaskRun(
            workflow_run_id=uuid4(),
            task_id=uuid4(),
            state=TaskRunState.RUNNING,
            lease_owner="worker-1",
            lease_expires_at=T0,
            fencing_token=1,
            started_at=T0,
        )
        assert run.lease_owner == "worker-1"
        assert run.is_terminal is False

    @pytest.mark.parametrize("attempt", [0, -1])
    def test_rejects_a_non_positive_attempt(self, attempt: int) -> None:
        """Attempts are 1-based so 'attempt 0' is never representable."""
        with pytest.raises(InvariantViolationError, match="attempt"):
            TaskRun(workflow_run_id=uuid4(), task_id=uuid4(), attempt=attempt)

    def test_rejects_a_negative_fencing_token(self) -> None:
        """A negative token would break monotonicity."""
        with pytest.raises(InvariantViolationError, match="fencing_token"):
            TaskRun(workflow_run_id=uuid4(), task_id=uuid4(), fencing_token=-1)

    def test_finished_at_cannot_precede_started_at(self) -> None:
        """Same rule as the workflow run."""
        with pytest.raises(InvariantViolationError, match="precede"):
            TaskRun(
                workflow_run_id=uuid4(),
                task_id=uuid4(),
                state=TaskRunState.SUCCEEDED,
                started_at=T0,
                finished_at=T0 - timedelta(seconds=1),
            )

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (TaskRunState.PENDING, False),
            (TaskRunState.READY, False),
            (TaskRunState.FAILED, False),
            (TaskRunState.RETRYING, False),
            (TaskRunState.SUCCEEDED, True),
            (TaskRunState.CANCELLED, True),
        ],
    )
    def test_is_terminal_matches_the_machine(self, state: TaskRunState, expected: bool) -> None:
        """``FAILED`` is retryable, so it is deliberately not terminal."""
        run = TaskRun(
            workflow_run_id=uuid4(),
            task_id=uuid4(),
            state=state,
            finished_at=T0 if expected else None,
        )
        assert run.is_terminal is expected
