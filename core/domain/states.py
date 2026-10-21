"""State vocabularies.

The enums hold *values*, not rules. Whether a transition is legal, and which
states are terminal, lives in :mod:`core.domain.machines` — one source of truth
rather than two that can disagree.

``StrEnum`` is used so a member compares equal to its database value without a
conversion step, while still failing loudly when an unknown string is coerced.
"""

from __future__ import annotations

from enum import StrEnum


class WorkflowRunState(StrEnum):
    """Lifecycle of one execution of a workflow definition."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskRunState(StrEnum):
    """Lifecycle of one task inside a workflow run.

    ``FAILED`` is not terminal: a failed task may be scheduled for another
    attempt via ``RETRYING``. ``SKIPPED`` is terminal and means the task's
    ``when`` condition was false — it did not run, and it is not a failure.
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"
    CANCELLED = "cancelled"
