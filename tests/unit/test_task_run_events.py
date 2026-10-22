"""The task-run event log: every transition is recorded, append-only.

These run on SQLite — recording an event is a row insert beside the transition,
so it needs no broker or PostgreSQL. The cancel-vs-completion race that decides
which of two competing events survives is covered in the integration suite.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from core.domain.states import TaskRunState
from core.models import Task, TaskRun, TaskRunEvent, Workflow, WorkflowRun
from core.repositories.transitions import (
    cancel_task_run,
    claim_task_run,
    finish_task_run,
    promote_due_retries,
    reclaim_task_run,
    requeue_dead_task_run,
    schedule_retries,
    transition_task_run,
)

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


def _ready_run() -> TaskRun:
    """A single-task run whose task run is ``READY`` and claimable."""
    workflow = Workflow.objects.create(name="etl", version=1)
    task = Task.objects.create(workflow=workflow, key="extract", task_type="extract")
    run = WorkflowRun.objects.create(workflow=workflow)
    return TaskRun.objects.create(workflow_run=run, task=task, state=TaskRunState.READY.value)


def _edges() -> list[tuple[str, str | None, str]]:
    """The recorded edges, oldest first, as ``(kind, from_state, to_state)``."""
    return [
        (event.kind, event.from_state, event.to_state)
        for event in TaskRunEvent.objects.order_by("created_at", "id")
    ]


def test_a_claim_then_finish_records_both_edges() -> None:
    """The two worker edges are each appended, in order."""
    task_run = _ready_run()
    token = claim_task_run(task_run_id=task_run.id, worker_id="w1", lease_seconds=30, now=NOW)
    assert token is not None
    assert finish_task_run(
        task_run_id=task_run.id,
        target=TaskRunState.SUCCEEDED,
        fencing_token=token,
        now=NOW,
    )

    assert _edges() == [
        ("claim", "ready", "running"),
        ("finish", "running", "succeeded"),
    ]
    assert all(event.task_run_id == task_run.id for event in TaskRunEvent.objects.all())


def test_a_lost_race_records_no_event() -> None:
    """A stale finish (wrong token) matches zero rows, so it records nothing."""
    task_run = _ready_run()
    token = claim_task_run(task_run_id=task_run.id, worker_id="w1", lease_seconds=30, now=NOW)
    assert token is not None
    assert (
        finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token + 999,
            now=NOW,
        )
        is False
    )

    assert [event.kind for event in TaskRunEvent.objects.all()] == ["claim"]


def test_a_promotion_to_ready_is_recorded_as_promote() -> None:
    """The scheduler's PENDING -> READY edge is a promotion."""
    task_run = _ready_run()
    TaskRun.objects.filter(pk=task_run.id).update(state=TaskRunState.PENDING.value)

    assert transition_task_run(
        task_run_id=task_run.id,
        expected=TaskRunState.PENDING,
        target=TaskRunState.READY,
        now=NOW,
    )

    assert _edges() == [("promote", "pending", "ready")]


def test_a_skipped_branch_is_recorded_as_skip() -> None:
    """An untaken branch (PENDING -> SKIPPED) is recorded, not left silent."""
    task_run = _ready_run()
    TaskRun.objects.filter(pk=task_run.id).update(state=TaskRunState.PENDING.value)

    assert transition_task_run(
        task_run_id=task_run.id,
        expected=TaskRunState.PENDING,
        target=TaskRunState.SKIPPED,
        now=NOW,
    )

    assert _edges() == [("skip", "pending", "skipped")]


def test_a_failure_then_retry_then_promotion_are_all_recorded() -> None:
    """The retry lifecycle records FAILED -> RETRYING (retry) and RETRYING -> READY."""
    task_run = _ready_run()
    TaskRun.objects.filter(pk=task_run.id).update(state=TaskRunState.FAILED.value, attempt=1)

    assert schedule_retries(now=NOW) == 1
    assert ("retry", "failed", "retrying") in _edges()

    # Once the backoff elapses the scheduler promotes it back to READY.
    assert promote_due_retries(now=NOW + timedelta(hours=1)) == 1
    assert ("promote", "retrying", "ready") in _edges()


def test_a_cancellation_is_recorded_with_no_from_state() -> None:
    """A cancel records the target but not the prior state (it is implied)."""
    task_run = _ready_run()

    assert cancel_task_run(task_run_id=task_run.id, now=NOW)

    assert _edges() == [("cancel", None, "cancelled")]


def test_a_reclaimed_lease_is_recorded_as_reclaim() -> None:
    """The reaper's RUNNING -> RETRYING edge is recorded after the claim."""
    task_run = _ready_run()
    token = claim_task_run(task_run_id=task_run.id, worker_id="w1", lease_seconds=30, now=NOW)
    assert token is not None
    TaskRun.objects.filter(pk=task_run.id).update(lease_expires_at=NOW - timedelta(seconds=1))

    assert reclaim_task_run(task_run_id=task_run.id, now=NOW + timedelta(seconds=1))

    assert ("reclaim", "running", "retrying") in _edges()


def test_a_requeue_from_dead_is_recorded() -> None:
    """The operator's DEAD -> PENDING recovery is recorded."""
    task_run = _ready_run()
    TaskRun.objects.filter(pk=task_run.id).update(state=TaskRunState.DEAD.value, finished_at=NOW)

    assert requeue_dead_task_run(task_run_id=task_run.id, now=NOW)

    assert _edges() == [("requeue", "dead", "pending")]


def test_a_failed_event_insert_rolls_back_the_state_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-013: the state write and its event share one transaction.

    If the event insert raises, the state change is rolled back with it — a
    durable state change can never outlive its audit entry. Before ADR-013 the
    finish path wrote the state in autocommit and the event separately, so a
    failed insert would have left the state moved and the event lost.
    """

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("event insert failed")

    task_run = _ready_run()
    token = claim_task_run(task_run_id=task_run.id, worker_id="w1", lease_seconds=30, now=NOW)
    assert token is not None

    # Only the finish path's event insert fails; the claim above already recorded.
    monkeypatch.setattr("core.repositories.transitions._record_task_run_event", _boom)

    with pytest.raises(RuntimeError, match="event insert failed"):
        finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token,
            now=NOW,
        )

    # The state change rolled back with the failed event insert.
    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.RUNNING.value
    assert [event.kind for event in TaskRunEvent.objects.all()] == ["claim"]
