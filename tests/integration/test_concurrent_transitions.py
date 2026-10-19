"""Concurrency on a real PostgreSQL server.

These cannot run on SQLite. SQLite serialises writers at the database level, so
a race test against it proves nothing: every thread simply queues. The whole
point of the conditional ``UPDATE`` is that PostgreSQL lets all N statements run
and decides the winner at row level.

Set ``REQUIRE_POSTGRES=1`` to turn the skip into a failure. CI does that, so a
misconfigured database fails the build instead of quietly skipping the only test
that exercises the guarantee.

The barrier, the per-thread connection cleanup and the no-stragglers check live
in ``tests/integration/conftest.py`` and are shared with the publish race tests.
"""

from __future__ import annotations

import pytest
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun, WorkflowRun
from core.repositories.transitions import (
    claim_task_run,
    finish_task_run,
    transition_task_run,
    transition_workflow_run,
)

from tests.conftest import NOW
from tests.integration.conftest import THREADS, require_postgres, run_concurrently

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]


def test_exactly_one_of_many_claims_wins(task_run: TaskRun) -> None:
    """N workers race for one READY task; exactly one may hold the lease."""
    require_postgres()
    task_run_id = task_run.id

    tokens = run_concurrently(
        THREADS,
        lambda index: claim_task_run(
            task_run_id=task_run_id,
            worker_id=f"worker-{index}",
            lease_seconds=60,
            now=NOW,
        ),
    )

    winners = [token for token in tokens if token is not None]
    assert len(tokens) == THREADS
    assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}: {winners}"
    assert winners[0] == 1, "the first claim must issue fencing token 1"

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.RUNNING.value
    assert task_run.fencing_token == 1
    assert task_run.lease_owner is not None
    assert task_run.lease_owner.startswith("worker-")


def test_the_winner_is_the_worker_recorded_on_the_row(task_run: TaskRun) -> None:
    """The token and the owner must come from the same claim."""
    require_postgres()
    task_run_id = task_run.id

    run_concurrently(
        THREADS,
        lambda index: claim_task_run(
            task_run_id=task_run_id,
            worker_id=f"worker-{index}",
            lease_seconds=60,
            now=NOW,
        ),
    )

    task_run.refresh_from_db()
    assert task_run.lease_owner is not None
    assert task_run.fencing_token == 1
    # Token 1 must belong to the owner stored on the row; if a losing claim had
    # written its own owner, the pair would be inconsistent.
    assert task_run.state == TaskRunState.RUNNING.value


def test_exactly_one_of_many_run_starts_wins(workflow_run: WorkflowRun) -> None:
    """The same guarantee for a workflow run, which has no lease to lean on."""
    require_postgres()
    run_id = workflow_run.id

    results = run_concurrently(
        THREADS,
        lambda _index: transition_workflow_run(
            run_id=run_id,
            expected=WorkflowRunState.PENDING,
            target=WorkflowRunState.RUNNING,
            now=NOW,
        ),
    )

    assert len(results) == THREADS
    assert sum(1 for won in results if won) == 1

    workflow_run.refresh_from_db()
    assert workflow_run.state == WorkflowRunState.RUNNING.value


def test_only_one_writer_can_finish_with_the_same_token(task_run: TaskRun) -> None:
    """Two threads holding the same token must not both write an outcome.

    This models the real failure: a worker retries its own write after a network
    blip while a second copy of it is still running.
    """
    require_postgres()
    token = claim_task_run(
        task_run_id=task_run.id,
        worker_id="worker-1",
        lease_seconds=60,
        now=NOW,
    )
    assert token is not None
    task_run_id = task_run.id

    results = run_concurrently(
        THREADS,
        lambda _index: finish_task_run(
            task_run_id=task_run_id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token,
            now=NOW,
        ),
    )

    assert sum(1 for won in results if won) == 1

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.SUCCEEDED.value


def test_a_stale_token_loses_even_under_concurrency(task_run: TaskRun) -> None:
    """Half the threads use a stale token, half the current one.

    Exactly the current-token writers may win; none of the stale ones may.
    """
    require_postgres()
    stale = claim_task_run(
        task_run_id=task_run.id, worker_id="worker-old", lease_seconds=60, now=NOW
    )
    assert stale == 1
    finish_task_run(
        task_run_id=task_run.id,
        target=TaskRunState.FAILED,
        fencing_token=stale,
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
    current = claim_task_run(
        task_run_id=task_run.id, worker_id="worker-new", lease_seconds=60, now=NOW
    )
    assert current == 2

    task_run_id = task_run.id

    def attempt(index: int) -> bool:
        token = stale if index % 2 == 0 else current
        return finish_task_run(
            task_run_id=task_run_id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token,
            now=NOW,
        )

    results = run_concurrently(THREADS, attempt)

    assert sum(1 for won in results if won) == 1, "only one current-token writer may win"

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.SUCCEEDED.value
