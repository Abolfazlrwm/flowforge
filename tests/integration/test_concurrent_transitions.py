"""Concurrency on a real PostgreSQL server.

These cannot run on SQLite. SQLite serialises writers at the database level, so
a race test against it proves nothing: every thread simply queues. The whole
point of the conditional ``UPDATE`` is that PostgreSQL lets all N statements run
and decides the winner at row level.

Threads are aligned with a :class:`threading.Barrier` rather than a ``sleep``.
A sleep makes the test slower and still not synchronised; a barrier means all N
threads are released within microseconds of each other, which is what makes the
race real. There is no sleep in this module.

Set ``REQUIRE_POSTGRES=1`` to turn the skip into a failure. CI does that, so a
misconfigured database fails the build instead of quietly skipping the only test
that exercises the guarantee.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import pytest
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun, WorkflowRun
from core.repositories.transitions import (
    claim_task_run,
    finish_task_run,
    transition_task_run,
    transition_workflow_run,
)
from django.db import connection

from tests.conftest import NOW

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]

THREADS = 16
TIMEOUT = 30.0


def _require_postgres() -> None:
    """Skip locally, fail in CI when PostgreSQL is required."""
    if connection.vendor == "postgresql":
        return
    if os.environ.get("REQUIRE_POSTGRES") == "1":
        pytest.fail(
            "REQUIRE_POSTGRES=1 but the active database is "
            f"{connection.vendor!r}. These tests are meaningless without PostgreSQL."
        )
    pytest.skip(f"concurrency semantics require PostgreSQL, not {connection.vendor!r}")


def _run_concurrently(count: int, work: Any) -> list[Any]:
    """Run ``work(index)`` in ``count`` threads released by one barrier.

    Every thread closes its own database connection afterwards: Django keeps
    connections per thread, and leaking them would exhaust the pool over a run.

    Returns:
        One result per thread, in the order the threads finished.

    Raises:
        AssertionError: if a thread raised, or did not finish within ``TIMEOUT``.
    """
    barrier = threading.Barrier(count)
    results: list[Any] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def target(index: int) -> None:
        try:
            barrier.wait(timeout=TIMEOUT)
            value = work(index)
        except BaseException as exc:  # reported below, never swallowed
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(value)
        finally:
            connection.close()

    threads = [threading.Thread(target=target, args=(i,), daemon=True) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=TIMEOUT)

    stragglers = [t for t in threads if t.is_alive()]
    assert not stragglers, (
        f"{len(stragglers)} thread(s) did not finish within {TIMEOUT}s — "
        "a deadlock or a missing row lock would look like this"
    )
    assert not errors, f"threads raised: {errors!r}"

    return results


def test_exactly_one_of_many_claims_wins(task_run: TaskRun) -> None:
    """N workers race for one READY task; exactly one may hold the lease."""
    _require_postgres()
    task_run_id = task_run.id

    tokens = _run_concurrently(
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
    _require_postgres()
    task_run_id = task_run.id

    _run_concurrently(
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
    _require_postgres()
    run_id = workflow_run.id

    results = _run_concurrently(
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
    _require_postgres()
    token = claim_task_run(
        task_run_id=task_run.id,
        worker_id="worker-1",
        lease_seconds=60,
        now=NOW,
    )
    assert token is not None
    task_run_id = task_run.id

    results = _run_concurrently(
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
    _require_postgres()
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

    results = _run_concurrently(THREADS, attempt)

    assert sum(1 for won in results if won) == 1, "only one current-token writer may win"

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.SUCCEEDED.value
