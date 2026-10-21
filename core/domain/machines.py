"""The two state machines FlowForge currently defines.

Both are built at import time, which means an inconsistent table raises
``ValueError`` during collection rather than at runtime. The invariants the
constructor enforces — no dead ends, no edges out of a terminal state, no
duplicate edges — are what make that guarantee worth having.
"""

from __future__ import annotations

from core.domain.states import TaskRunState, WorkflowRunState
from core.domain.transitions import StateMachine, Transition

WORKFLOW_RUN_MACHINE: StateMachine[WorkflowRunState] = StateMachine(
    name="WorkflowRun",
    states=WorkflowRunState,
    terminal=(
        WorkflowRunState.SUCCEEDED,
        WorkflowRunState.FAILED,
        WorkflowRunState.CANCELLED,
    ),
    transitions=(
        Transition(
            name="start",
            source=WorkflowRunState.PENDING,
            target=WorkflowRunState.RUNNING,
        ),
        Transition(
            name="cancel_before_start",
            source=WorkflowRunState.PENDING,
            target=WorkflowRunState.CANCELLED,
        ),
        Transition(
            name="succeed",
            source=WorkflowRunState.RUNNING,
            target=WorkflowRunState.SUCCEEDED,
        ),
        Transition(
            name="fail",
            source=WorkflowRunState.RUNNING,
            target=WorkflowRunState.FAILED,
        ),
        Transition(
            name="cancel",
            source=WorkflowRunState.RUNNING,
            target=WorkflowRunState.CANCELLED,
        ),
    ),
)

TASK_RUN_MACHINE: StateMachine[TaskRunState] = StateMachine(
    name="TaskRun",
    states=TaskRunState,
    terminal=(
        TaskRunState.SUCCEEDED,
        TaskRunState.DEAD,
        TaskRunState.CANCELLED,
    ),
    transitions=(
        Transition(
            name="dependencies_satisfied",
            source=TaskRunState.PENDING,
            target=TaskRunState.READY,
        ),
        Transition(
            name="cancel_pending",
            source=TaskRunState.PENDING,
            target=TaskRunState.CANCELLED,
        ),
        # Claiming acquires the lease, so it does not require one.
        Transition(
            name="claim",
            source=TaskRunState.READY,
            target=TaskRunState.RUNNING,
        ),
        Transition(
            name="cancel_ready",
            source=TaskRunState.READY,
            target=TaskRunState.CANCELLED,
        ),
        # Finishing requires the lease: only the worker holding the current
        # fencing token may write a terminal outcome.
        Transition(
            name="succeed",
            source=TaskRunState.RUNNING,
            target=TaskRunState.SUCCEEDED,
            requires_lease=True,
        ),
        Transition(
            name="fail",
            source=TaskRunState.RUNNING,
            target=TaskRunState.FAILED,
            requires_lease=True,
        ),
        Transition(
            name="cancel_running",
            source=TaskRunState.RUNNING,
            target=TaskRunState.CANCELLED,
            requires_lease=True,
        ),
        # The lease reaper. A RUNNING task whose lease has expired is not simply
        # returned to READY — it counts as a failed attempt and is routed by the
        # reaper into the retry lifecycle: either RETRYING (if attempts remain) or
        # DEAD (if the policy is exhausted). The reaper does NOT require a lease:
        # it forcibly reclaims an expired one on behalf of the scheduler. These are
        # the only edges that move a task backwards, and that is intentional — a
        # dead worker must not pin a task run in RUNNING forever.
        Transition(
            name="reap_retry",
            source=TaskRunState.RUNNING,
            target=TaskRunState.RETRYING,
        ),
        Transition(
            name="reap_dead",
            source=TaskRunState.RUNNING,
            target=TaskRunState.DEAD,
        ),
        # Retries are scheduled by the scheduler once a task reaches FAILED (or,
        # for a dead worker, by the reaper directly). Too many attempts and the
        # task is buried: a terminal state the operator must requeue by hand.
        Transition(
            name="schedule_retry",
            source=TaskRunState.FAILED,
            target=TaskRunState.RETRYING,
        ),
        Transition(
            name="bury",
            source=TaskRunState.FAILED,
            target=TaskRunState.DEAD,
        ),
        Transition(
            name="retry_ready",
            source=TaskRunState.RETRYING,
            target=TaskRunState.READY,
        ),
    ),
)

MACHINES = (WORKFLOW_RUN_MACHINE, TASK_RUN_MACHINE)
