"""Cancellation, and the race between cancelling and completing.

Cancellation is an *operator* action, so it cannot use the fencing-token path —
the caller has no lease. It uses the same conditional-``UPDATE`` shape as the
reaper instead, which is what makes it safe against a worker finishing at the same
instant: one statement wins, the other matches zero rows.

Both orders are tested, because "the loser does nothing" is the whole guarantee
and only one of them is the common case.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import Task, TaskRun, Workflow, WorkflowRun
from core.repositories.transitions import (
    cancel_task_run,
    claim_task_run,
    finish_task_run,
)
from core.services.runs import request_cancellation

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


class TestCancelTaskRun:
    """One task, in each state a cancellation can meet it in."""

    def test_a_pending_task_is_cancelled(self, task_run: TaskRun) -> None:
        """A pending task is cancelled."""
        TaskRun.objects.filter(pk=task_run.pk).update(state=TaskRunState.PENDING.value)

        assert cancel_task_run(task_run_id=task_run.id, now=NOW)

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.CANCELLED.value
        assert task_run.finished_at == NOW

    def test_a_running_task_is_cancelled_and_its_worker_invalidated(
        self, task_run: TaskRun
    ) -> None:
        """A running task is cancelled and its worker invalidated.

        The token is bumped, so the in-flight handler's finish is discarded.
        """
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        assert token is not None

        assert cancel_task_run(task_run_id=task_run.id, now=NOW + timedelta(seconds=1))

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.CANCELLED.value
        assert task_run.fencing_token == token + 1
        assert task_run.lease_owner is None
        assert task_run.lease_expires_at is None

        # The handler finishes a moment later and is told its write did not land.
        assert (
            finish_task_run(
                task_run_id=task_run.id,
                target=TaskRunState.SUCCEEDED,
                fencing_token=token,
                result={"late": True},
                now=NOW + timedelta(seconds=2),
            )
            is False
        )
        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.CANCELLED.value
        assert task_run.result is None

    def test_a_retrying_task_is_cancelled_and_loses_its_backoff(self, task_run: TaskRun) -> None:
        """A retry waiting out its delay must not be promoted after a cancel.

        The CHECK constraint requires ``scheduled_at`` to be null outside
        ``RETRYING``, so this also proves the cancel clears it.
        """
        TaskRun.objects.filter(pk=task_run.pk).update(
            state=TaskRunState.RETRYING.value,
            scheduled_at=NOW + timedelta(seconds=30),
            attempt=2,
        )

        assert cancel_task_run(task_run_id=task_run.id, now=NOW)

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.CANCELLED.value
        assert task_run.scheduled_at is None

    @pytest.mark.parametrize(
        "state",
        [TaskRunState.SUCCEEDED, TaskRunState.DEAD, TaskRunState.CANCELLED],
    )
    def test_a_resolved_task_is_left_alone(self, task_run: TaskRun, state: TaskRunState) -> None:
        """Cancelling a finished task would rewrite an outcome that already happened."""
        TaskRun.objects.filter(pk=task_run.pk).update(state=state.value)

        assert cancel_task_run(task_run_id=task_run.id, now=NOW) is False

        task_run.refresh_from_db()
        assert task_run.state == state.value

    def test_a_missing_row_is_a_miss_not_an_error(self) -> None:
        """A missing row is a miss not an error."""
        assert cancel_task_run(task_run_id=uuid4(), now=NOW) is False


class TestCancelVersusCompletion:
    """Whichever conditional UPDATE runs first wins; the loser is a no-op."""

    def test_cancellation_beats_a_later_completion(self, task_run: TaskRun) -> None:
        """Cancellation beats a later completion."""
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        assert cancel_task_run(task_run_id=task_run.id, now=NOW)

        assert not finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token,
            now=NOW,
        )

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.CANCELLED.value

    def test_completion_beats_a_later_cancellation(self, task_run: TaskRun) -> None:
        """A task that already succeeded is not retroactively cancelled."""
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        assert finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token,
            now=NOW,
        )

        assert cancel_task_run(task_run_id=task_run.id, now=NOW) is False

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.SUCCEEDED.value


class TestRequestCancellation:
    """Cancelling a whole run propagates to every task that has not resolved."""

    def _run_with_tasks(self) -> tuple[WorkflowRun, dict[str, TaskRun]]:
        workflow = Workflow.objects.create(name="cancel", version=1)
        run = WorkflowRun.objects.create(workflow=workflow, state=WorkflowRunState.RUNNING.value)
        runs: dict[str, TaskRun] = {}
        for key, state in [
            ("pending", TaskRunState.PENDING),
            ("ready", TaskRunState.READY),
            ("running", TaskRunState.RUNNING),
            ("done", TaskRunState.SUCCEEDED),
            ("skipped", TaskRunState.SKIPPED),
        ]:
            task = Task.objects.create(workflow=workflow, key=key, task_type="noop")
            # The `taskrun_lease_iff_running` CHECK requires a lease exactly while
            # a task is running, so the RUNNING row must carry one at INSERT time.
            lease: dict[str, object] = (
                {
                    "lease_owner": "worker-1",
                    "lease_expires_at": NOW + timedelta(seconds=30),
                    "fencing_token": 1,
                }
                if state is TaskRunState.RUNNING
                else {}
            )
            runs[key] = TaskRun.objects.create(
                workflow_run=run,
                task=task,
                state=state.value,
                **lease,  # type: ignore[arg-type]
            )
        return run, runs

    def test_every_unresolved_task_is_cancelled(self) -> None:
        """Every unresolved task is cancelled."""
        run, runs = self._run_with_tasks()

        assert request_cancellation(run.id, now=NOW) == 3

        for key in ("pending", "ready", "running"):
            runs[key].refresh_from_db()
            assert runs[key].state == TaskRunState.CANCELLED.value

        # Resolved tasks keep their outcome.
        runs["done"].refresh_from_db()
        runs["skipped"].refresh_from_db()
        assert runs["done"].state == TaskRunState.SUCCEEDED.value
        assert runs["skipped"].state == TaskRunState.SKIPPED.value

        run.refresh_from_db()
        assert run.state == WorkflowRunState.CANCELLED.value
        assert run.finished_at == NOW

    @pytest.mark.parametrize(
        "state",
        [
            WorkflowRunState.SUCCEEDED,
            WorkflowRunState.FAILED,
            WorkflowRunState.CANCELLED,
        ],
    )
    def test_a_terminal_run_is_left_alone(self, state: WorkflowRunState) -> None:
        """Cancelling a finished run would rewrite history and stop nothing."""
        run, runs = self._run_with_tasks()
        # A terminal run must carry a finish time (wfrun_finished_at_matches_state).
        WorkflowRun.objects.filter(pk=run.pk).update(state=state.value, finished_at=NOW)

        assert request_cancellation(run.id, now=NOW) == 0

        run.refresh_from_db()
        assert run.state == state.value
        runs["ready"].refresh_from_db()
        assert runs["ready"].state == TaskRunState.READY.value

    def test_a_missing_run_raises(self) -> None:
        """A missing run raises."""
        with pytest.raises(WorkflowRun.DoesNotExist):
            request_cancellation(uuid4(), now=NOW)
