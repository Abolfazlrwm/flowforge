"""Domain entities.

Plain dataclasses, no Django. They exist so the invariants of an execution can
be stated and tested without a database, and so that code which only needs the
rules — a worker deciding whether it may write a result, a CLI validating a
request — does not drag in the ORM.

The ORM models in :mod:`core.models` mirror these fields and enforce the same
rules with CHECK constraints. The duplication is deliberate: one is the
in-memory contract, the other is the guarantee that survives a process crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from core.domain.errors import InvariantViolationError
from core.domain.machines import TASK_RUN_MACHINE, WORKFLOW_RUN_MACHINE
from core.domain.states import TaskRunState, WorkflowRunState


@dataclass(frozen=True, slots=True)
class Workflow:
    """A versioned workflow definition."""

    name: str
    version: int = 1
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate identity fields."""
        if not self.name.strip():
            raise InvariantViolationError("workflow name must not be blank")
        if self.version < 1:
            raise InvariantViolationError(f"workflow version must be >= 1, got {self.version}")


@dataclass(frozen=True, slots=True)
class Task:
    """One step belonging to a workflow definition."""

    workflow_id: UUID
    key: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate identity fields."""
        if not self.key.strip():
            raise InvariantViolationError("task key must not be blank")


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    """One execution of a :class:`Workflow`."""

    workflow_id: UUID
    state: WorkflowRunState = WorkflowRunState.PENDING
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        """Coerce the state and enforce the timestamps' relationship to it."""
        object.__setattr__(self, "state", WorkflowRunState(self.state))
        terminal = WORKFLOW_RUN_MACHINE.is_terminal(self.state)
        if terminal and self.finished_at is None:
            raise InvariantViolationError(
                f"workflow run in terminal state {self.state.value!r} must have finished_at"
            )
        if not terminal and self.finished_at is not None:
            raise InvariantViolationError(
                f"workflow run in non-terminal state {self.state.value!r} must not have finished_at"
            )
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise InvariantViolationError("finished_at must not precede started_at")


@dataclass(frozen=True, slots=True)
class TaskRun:
    """One attempt at executing a :class:`Task` inside a :class:`WorkflowRun`."""

    workflow_run_id: UUID
    task_id: UUID
    state: TaskRunState = TaskRunState.PENDING
    attempt: int = 1
    fencing_token: int = 0
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        """Coerce the state and enforce the invariants the database also checks."""
        object.__setattr__(self, "state", TaskRunState(self.state))

        if self.attempt < 1:
            raise InvariantViolationError(f"attempt must be >= 1, got {self.attempt}")
        if self.fencing_token < 0:
            raise InvariantViolationError(f"fencing_token must be >= 0, got {self.fencing_token}")

        holds_lease = self.lease_expires_at is not None
        if self.state is TaskRunState.RUNNING and not holds_lease:
            raise InvariantViolationError(
                "a running task run must hold a lease; without one a crashed worker "
                "could never be distinguished from a slow one"
            )
        if self.state is not TaskRunState.RUNNING and holds_lease:
            raise InvariantViolationError(
                f"only a running task run may hold a lease, not {self.state.value!r}"
            )
        if holds_lease and not self.lease_owner:
            raise InvariantViolationError("a held lease must name its owner")

        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise InvariantViolationError("finished_at must not precede started_at")

    @property
    def is_terminal(self) -> bool:
        """Whether this task run can never change state again."""
        return TASK_RUN_MACHINE.is_terminal(self.state)
