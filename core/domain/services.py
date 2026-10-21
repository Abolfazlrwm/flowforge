"""Pure domain services.

Rules that involve more than one entity but no I/O. Keeping them here means
they can be tested with a list of enum members instead of a database, and it
keeps the decision about *what a run's state means* out of both the view and
the worker.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.domain.errors import InvariantViolationError
from core.domain.states import TaskRunState, WorkflowRunState

#: States that mean "this run is over", independent of how it ended.
#: ``DEAD`` (retry policy exhausted) is over; ``FAILED`` is *not* — it may still be
#: retried and recover — so it is deliberately excluded.
FINISHED_TASK_STATES = frozenset(
    {
        TaskRunState.SUCCEEDED,
        TaskRunState.DEAD,
        TaskRunState.CANCELLED,
    }
)


def initial_task_run_state(*, dependency_count: int) -> TaskRunState:
    """State a task run starts in, given how many tasks it waits on.

    A task with no dependencies is immediately claimable, so it starts ``READY``
    rather than ``PENDING``. With dependencies it starts ``PENDING`` and is moved
    to ``READY`` when they complete (Phase 2 resolves the DAG; the rule itself
    does not change).

    Raises:
        InvariantViolationError: if ``dependency_count`` is negative.
    """
    if dependency_count < 0:
        raise InvariantViolationError(f"dependency_count must be >= 0, got {dependency_count}")
    return TaskRunState.READY if dependency_count == 0 else TaskRunState.PENDING


def derive_workflow_run_state(states: Iterable[TaskRunState]) -> WorkflowRunState:
    """Fold the states of a run's task runs into the run's own state.

    The order of the checks is the policy, and it is deliberate:

    1. Any cancellation makes the run cancelled — a user asked for it to stop.
    2. Any failure or dead task makes the run failed, even if other tasks are
       still running. A ``DEAD`` task (retry policy exhausted) is a permanent
       failure, so it is treated the same as a ``FAILED`` one. Reporting
       ``RUNNING`` here would hide a failure behind work that is merely slow.
    3. All succeeded means succeeded.
    4. Otherwise the run is still in flight: ``RUNNING`` once anything has
       started (a task in ``RETRYING`` is still in flight, not pending),
       ``PENDING`` while nothing has.

    An empty sequence is ``PENDING``: a run with no tasks has not started.

    Raises:
        InvariantViolationError: if a value is not a :class:`TaskRunState`.
    """
    resolved: list[TaskRunState] = []
    for state in states:
        if not isinstance(state, TaskRunState):
            raise InvariantViolationError(f"expected TaskRunState, got {state!r}")
        resolved.append(state)

    if not resolved:
        return WorkflowRunState.PENDING
    if any(state is TaskRunState.CANCELLED for state in resolved):
        return WorkflowRunState.CANCELLED
    if any(state is TaskRunState.FAILED or state is TaskRunState.DEAD for state in resolved):
        return WorkflowRunState.FAILED
    if all(state is TaskRunState.SUCCEEDED for state in resolved):
        return WorkflowRunState.SUCCEEDED
    if any(state is not TaskRunState.PENDING for state in resolved):
        return WorkflowRunState.RUNNING
    return WorkflowRunState.PENDING
