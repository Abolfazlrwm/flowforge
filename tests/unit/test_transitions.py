"""Atomic conditional-UPDATE transitions.

The distinction these tests exist to protect:

- an **illegal** transition raises, because the caller is wrong;
- a **legal** transition that loses a race returns ``False``, because the
  caller is merely late.

Collapsing those two into one outcome would make a lost race look like a bug and
a genuine bug look like contention.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from core.domain.errors import IllegalTransitionError, LeaseRequiredError
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun, WorkflowRun
from core.repositories.transitions import (
    claim_task_run,
    finish_task_run,
    reap_expired_leases,
    reclaim_task_run,
    renew_lease,
    transition_task_run,
    transition_workflow_run,
)

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


class TestWorkflowRunTransitions:
    """Conditional updates on a workflow run."""

    def test_first_transition_wins(self, workflow_run: WorkflowRun) -> None:
        """The caller that performs the change is told so."""
        assert (
            transition_workflow_run(
                run_id=workflow_run.id,
                expected=WorkflowRunState.PENDING,
                target=WorkflowRunState.RUNNING,
                now=NOW,
            )
            is True
        )

        workflow_run.refresh_from_db()
        assert workflow_run.state == WorkflowRunState.RUNNING.value
        assert workflow_run.started_at == NOW

    def test_second_identical_transition_loses(self, workflow_run: WorkflowRun) -> None:
        """The conditional UPDATE must not match a row that already moved."""
        assert transition_workflow_run(
            run_id=workflow_run.id,
            expected=WorkflowRunState.PENDING,
            target=WorkflowRunState.RUNNING,
            now=NOW,
        )
        assert (
            transition_workflow_run(
                run_id=workflow_run.id,
                expected=WorkflowRunState.PENDING,
                target=WorkflowRunState.RUNNING,
                now=NOW,
            )
            is False
        )

    def test_terminal_transition_stamps_finished_at(self, workflow_run: WorkflowRun) -> None:
        """A terminal state without a timestamp would break every duration metric."""
        transition_workflow_run(
            run_id=workflow_run.id,
            expected=WorkflowRunState.PENDING,
            target=WorkflowRunState.RUNNING,
            now=NOW,
        )
        finished = NOW + timedelta(seconds=30)

        assert transition_workflow_run(
            run_id=workflow_run.id,
            expected=WorkflowRunState.RUNNING,
            target=WorkflowRunState.SUCCEEDED,
            now=finished,
        )

        workflow_run.refresh_from_db()
        assert workflow_run.finished_at == finished

    def test_cancelling_before_start_leaves_started_at_empty(
        self, workflow_run: WorkflowRun
    ) -> None:
        """A run cancelled while pending never started, and should not pretend to."""
        assert transition_workflow_run(
            run_id=workflow_run.id,
            expected=WorkflowRunState.PENDING,
            target=WorkflowRunState.CANCELLED,
            now=NOW,
        )

        workflow_run.refresh_from_db()
        assert workflow_run.state == WorkflowRunState.CANCELLED.value
        assert workflow_run.started_at is None
        assert workflow_run.finished_at == NOW

    def test_illegal_transition_raises_before_touching_the_database(
        self, workflow_run: WorkflowRun
    ) -> None:
        """A programmer error must not depend on what the row happens to contain."""
        with pytest.raises(IllegalTransitionError):
            transition_workflow_run(
                run_id=workflow_run.id,
                expected=WorkflowRunState.SUCCEEDED,
                target=WorkflowRunState.RUNNING,
            )

        workflow_run.refresh_from_db()
        assert workflow_run.state == WorkflowRunState.PENDING.value

    def test_illegal_transition_raises_even_when_the_row_does_not_exist(self) -> None:
        """The check is on the request, not on the row."""
        with pytest.raises(IllegalTransitionError):
            transition_workflow_run(
                run_id=uuid.uuid4(),
                expected=WorkflowRunState.CANCELLED,
                target=WorkflowRunState.RUNNING,
            )

    def test_a_missing_row_loses_rather_than_raising(self, workflow_run: WorkflowRun) -> None:
        """A legal transition against a row that is gone is a lost race, not an error."""
        # The fixture creates a real row; the transition targets a different id,
        # which is what a caller holding a stale identifier would do.
        assert workflow_run.pk is not None
        assert (
            transition_workflow_run(
                run_id=uuid.uuid4(),
                expected=WorkflowRunState.PENDING,
                target=WorkflowRunState.RUNNING,
            )
            is False
        )


class TestTaskRunTransitions:
    """Conditional updates on a task run."""

    def test_pending_to_ready(self, task_run: TaskRun) -> None:
        """Dependency satisfaction is a plain conditional update."""
        TaskRun.objects.filter(pk=task_run.pk).update(state=TaskRunState.PENDING.value)

        assert transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.PENDING,
            target=TaskRunState.READY,
        )
        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.READY.value

    def test_retry_increments_attempt_and_clears_the_previous_finish(
        self, task_run: TaskRun
    ) -> None:
        """A retry reuses the row, so the attempt counter is the only history."""
        TaskRun.objects.filter(pk=task_run.pk).update(
            state=TaskRunState.FAILED.value,
            started_at=NOW,
            finished_at=NOW,
        )

        assert transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.FAILED,
            target=TaskRunState.RETRYING,
        )

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RETRYING.value
        assert task_run.attempt == 2
        assert task_run.finished_at is None

    def test_cancelling_clears_the_lease(self, task_run: TaskRun) -> None:
        """A cancelled row must not keep a lease, or the invariant check fires."""
        TaskRun.objects.filter(pk=task_run.pk).update(
            state=TaskRunState.RUNNING.value,
            lease_owner="worker-1",
            lease_expires_at=NOW,
            fencing_token=1,
        )

        assert finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.CANCELLED,
            fencing_token=1,
            now=NOW,
        )

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.CANCELLED.value
        assert task_run.lease_owner is None
        assert task_run.lease_expires_at is None

    def test_lease_requiring_edges_are_refused_by_the_generic_path(self, task_run: TaskRun) -> None:
        """Completing a task must go through the fencing-token path."""
        TaskRun.objects.filter(pk=task_run.pk).update(
            state=TaskRunState.RUNNING.value,
            lease_owner="worker-1",
            lease_expires_at=NOW,
            fencing_token=1,
        )

        with pytest.raises(LeaseRequiredError):
            transition_task_run(
                task_run_id=task_run.id,
                expected=TaskRunState.RUNNING,
                target=TaskRunState.SUCCEEDED,
            )


class TestClaiming:
    """Claiming is where the fencing token is issued."""

    def test_first_claim_wins_and_issues_token_one(self, task_run: TaskRun) -> None:
        """Token 0 means 'never claimed'; the first claim must move it to 1."""
        assert task_run.fencing_token == 0

        token = claim_task_run(
            task_run_id=task_run.id,
            worker_id="worker-1",
            lease_seconds=30,
            now=NOW,
        )

        assert token == 1
        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RUNNING.value
        assert task_run.lease_owner == "worker-1"
        assert task_run.lease_expires_at == NOW + timedelta(seconds=30)
        assert task_run.started_at == NOW

    def test_second_claim_loses(self, task_run: TaskRun) -> None:
        """A claimed row is no longer READY, so the UPDATE matches nothing."""
        claim_task_run(task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW)

        assert (
            claim_task_run(task_run_id=task_run.id, worker_id="worker-2", lease_seconds=30, now=NOW)
            is None
        )

        task_run.refresh_from_db()
        assert task_run.lease_owner == "worker-1"
        assert task_run.fencing_token == 1

    def test_token_increments_monotonically_across_attempts(self, task_run: TaskRun) -> None:
        """Each claim must strictly increase the token, or fencing is meaningless."""
        first = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.FAILED,
            fencing_token=first,
            now=NOW,
        )
        transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.FAILED,
            target=TaskRunState.RETRYING,
        )
        transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.RETRYING,
            target=TaskRunState.READY,
        )
        second = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-2", lease_seconds=30, now=NOW
        )

        assert first == 1
        assert second == 2
        assert second > first

    def test_retry_claim_keeps_the_original_start_time(self, task_run: TaskRun) -> None:
        """Otherwise 'time to first start' would reset on every retry."""
        first_start = NOW
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=first_start
        )
        finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.FAILED,
            fencing_token=token,
            now=NOW,
        )
        transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.FAILED,
            target=TaskRunState.RETRYING,
        )
        transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.RETRYING,
            target=TaskRunState.READY,
        )
        later = NOW + timedelta(minutes=5)
        claim_task_run(task_run_id=task_run.id, worker_id="worker-2", lease_seconds=30, now=later)

        task_run.refresh_from_db()
        assert task_run.started_at == first_start
        assert task_run.attempt == 2

    @pytest.mark.parametrize(
        ("worker_id", "lease_seconds"),
        [("", 30), ("   ", 30), ("worker-1", 0), ("worker-1", -5)],
    )
    def test_invalid_claim_parameters_are_refused(
        self, task_run: TaskRun, worker_id: str, lease_seconds: int
    ) -> None:
        """A blank owner or non-positive lease cannot be enforced, so refuse it."""
        with pytest.raises(ValueError):
            claim_task_run(
                task_run_id=task_run.id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )


class TestFencing:
    """The fencing token is what makes a resurrected worker harmless."""

    def test_current_holder_can_finish(self, task_run: TaskRun) -> None:
        """The happy path."""
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )

        assert finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token,
            now=NOW,
        )

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.SUCCEEDED.value

    def test_a_stale_token_cannot_overwrite_a_newer_attempt(self, task_run: TaskRun) -> None:
        """This is the whole point of the token.

        Worker A claims token 1, is presumed dead, worker B claims token 2 and
        succeeds. When A wakes up and writes, its UPDATE must match nothing —
        otherwise A's stale result would replace B's.
        """
        stale_token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-A", lease_seconds=30, now=NOW
        )
        finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.FAILED,
            fencing_token=stale_token,
            now=NOW,
        )
        transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.FAILED,
            target=TaskRunState.RETRYING,
        )
        transition_task_run(
            task_run_id=task_run.id,
            expected=TaskRunState.RETRYING,
            target=TaskRunState.READY,
        )
        fresh_token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-B", lease_seconds=30, now=NOW
        )

        assert fresh_token == stale_token + 1

        # Worker A wakes up and tries to write with its old token.
        assert (
            finish_task_run(
                task_run_id=task_run.id,
                target=TaskRunState.SUCCEEDED,
                fencing_token=stale_token,
                now=NOW,
            )
            is False
        )

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RUNNING.value
        assert task_run.lease_owner == "worker-B"

    def test_finishing_an_unclaimed_task_loses(self, task_run: TaskRun) -> None:
        """Token 0 is never a valid claim, so nothing may be written with it."""
        assert (
            finish_task_run(
                task_run_id=task_run.id,
                target=TaskRunState.SUCCEEDED,
                fencing_token=0,
                now=NOW,
            )
            is False
        )

    def test_illegal_finish_target_raises(self, task_run: TaskRun) -> None:
        """``RUNNING -> READY`` is not an edge; the caller is wrong, not late."""
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )

        with pytest.raises(IllegalTransitionError):
            finish_task_run(
                task_run_id=task_run.id,
                target=TaskRunState.READY,
                fencing_token=token,
            )


class TestLeaseReaper:
    """The reaper returns an expired lease to READY and invalidates the dead worker."""

    def test_reap_returns_an_expired_lease_to_ready(self, task_run: TaskRun) -> None:
        """A RUNNING row past its lease expiry must become re-dispatchable."""
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        assert token == 1
        # Move the lease expiry into the past so the row is now stale.
        TaskRun.objects.filter(pk=task_run.id).update(lease_expires_at=NOW - timedelta(seconds=1))

        assert reclaim_task_run(task_run_id=task_run.id, now=NOW + timedelta(seconds=1))

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.READY.value
        # The lease is gone; the CHECK constraint demands exactly this.
        assert task_run.lease_owner is None
        assert task_run.lease_expires_at is None
        # And the fencing token advanced, so the dead worker's token is stale.
        assert task_run.fencing_token == 2

    def test_a_live_lease_is_not_reaped(self, task_run: TaskRun) -> None:
        """A row whose lease has not expired must stay RUNNING."""
        claim_task_run(task_run_id=task_run.id, worker_id="worker-1", lease_seconds=300, now=NOW)

        assert reclaim_task_run(task_run_id=task_run.id, now=NOW) is False

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RUNNING.value
        assert task_run.lease_owner == "worker-1"

    def test_reap_is_a_lost_race_not_an_error_for_a_gone_row(self, task_run: TaskRun) -> None:
        """Reclaiming a row that no longer exists is a miss, not an exception."""
        assert reclaim_task_run(task_run_id=uuid.uuid4(), now=NOW) is False

    def test_a_resurrected_worker_cannot_write_after_reap(self, task_run: TaskRun) -> None:
        """The whole point of the reaper. The dead worker's finish must be refused.

        Worker A claims token 1, its lease expires, the reaper reclaims the row
        (token -> 2, state -> READY). When A wakes up and writes with token 1, the
        conditional UPDATE matches nothing: the row is no longer RUNNING and its
        token has moved on. Without the reaper, A would silently overwrite a newer
        outcome; with it, A's write is a no-op.
        """
        stale_token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-A", lease_seconds=30, now=NOW
        )
        assert stale_token == 1
        TaskRun.objects.filter(pk=task_run.id).update(lease_expires_at=NOW - timedelta(seconds=1))

        assert reclaim_task_run(task_run_id=task_run.id, now=NOW + timedelta(seconds=1))

        # Worker A wakes up, believing it still owns the row.
        assert (
            finish_task_run(
                task_run_id=task_run.id,
                target=TaskRunState.SUCCEEDED,
                fencing_token=stale_token,
                now=NOW,
            )
            is False
        )

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.READY.value
        assert task_run.fencing_token == 2

    def test_reap_expired_leases_counts_only_expired_rows(self, task_run: TaskRun) -> None:
        """The bulk scanner reaps exactly the rows whose lease is past, nothing else."""
        claim_task_run(task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW)
        # Expire this lease.
        TaskRun.objects.filter(pk=task_run.id).update(lease_expires_at=NOW - timedelta(seconds=1))

        assert reap_expired_leases(now=NOW + timedelta(seconds=1)) == 1

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.READY.value

    def test_reap_expired_leases_reports_zero_when_nothing_is_expired(
        self, task_run: TaskRun
    ) -> None:
        """No expired leases means no reclamations, and no churn."""
        claim_task_run(task_run_id=task_run.id, worker_id="worker-1", lease_seconds=300, now=NOW)
        assert reap_expired_leases(now=NOW) == 0

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RUNNING.value

    def test_reaper_edge_is_not_reachable_through_transition_task_run(
        self, task_run: TaskRun
    ) -> None:
        """The reaper transition has side effects only ``reclaim_task_run`` performs.

        Routing it through ``transition_task_run`` would move the state but leave the
        lease and fencing token untouched, violating the lease invariant. It must be
        refused with a pointer to the correct function.
        """
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        TaskRun.objects.filter(pk=task_run.id).update(lease_expires_at=NOW - timedelta(seconds=1))

        with pytest.raises(IllegalTransitionError):
            transition_task_run(
                task_run_id=task_run.id,
                expected=TaskRunState.RUNNING,
                target=TaskRunState.READY,
            )

        # The row is left untouched — only ``reclaim_task_run`` may change it.
        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RUNNING.value
        assert task_run.fencing_token == token


class TestLeaseHeartbeat:
    """``renew_lease`` extends a held lease and refuses a stale one."""

    def test_renew_lease_extends_a_held_lease(self, task_run: TaskRun) -> None:
        """The heartbeat can push the expiry out while the worker still owns the row."""
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        assert token == 1

        assert renew_lease(task_run_id=task_run.id, fencing_token=token, lease_seconds=90, now=NOW)

        task_run.refresh_from_db()
        assert task_run.lease_expires_at == NOW + timedelta(seconds=90)
        assert task_run.state == TaskRunState.RUNNING.value

    def test_renew_lease_refuses_a_stale_token(self, task_run: TaskRun) -> None:
        """A worker whose lease was reaped cannot extend the new generation's lease."""
        token = claim_task_run(
            task_run_id=task_run.id, worker_id="worker-1", lease_seconds=30, now=NOW
        )
        assert token == 1
        TaskRun.objects.filter(pk=task_run.id).update(lease_expires_at=NOW - timedelta(seconds=1))
        assert reclaim_task_run(task_run_id=task_run.id, now=NOW + timedelta(seconds=1))

        # The reaper bumped the token, so the dead worker's token no longer matches.
        assert (
            renew_lease(
                task_run_id=task_run.id,
                fencing_token=token,
                lease_seconds=90,
                now=NOW + timedelta(seconds=2),
            )
            is False
        )

    def test_renew_lease_rejects_a_non_positive_lease(self, task_run: TaskRun) -> None:
        """A zero lease would be instantly expired; reject it rather than write it."""
        with pytest.raises(ValueError):
            renew_lease(task_run_id=task_run.id, fencing_token=1, lease_seconds=0)
