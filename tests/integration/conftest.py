"""Shared machinery for the concurrency tests.

This started life inside ``test_concurrent_transitions.py``. It moved out when
the publish race was added, because that test needs the same barrier, the same
per-thread connection cleanup and the same "no stragglers" check — but unlike
the transition races, its losers are *expected* to raise. Asserting that no
thread raised, which is right for a claim race, would have been wrong there, so
the two cases get two entry points over one implementation.

Two invariants are shared by every user of this module:

1. **No sleeps.** Threads are released by a :class:`threading.Barrier`, so all N
   run within microseconds of each other. A sleep makes a race test slower and
   still not synchronised.
2. **No leaked connections.** Django opens one connection per thread, so each
   thread closes its own in a ``finally``. Without it the pool drains over a
   run.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import pytest
from django.db import connection

THREADS = 16
TIMEOUT = 30.0


def require_postgres() -> None:
    """Skip locally, fail in CI when PostgreSQL is required.

    SQLite serialises writers at the database level, so every thread simply
    queues and a race test against it proves nothing. The point of a race test
    is that PostgreSQL lets all N statements run and decides the winner at row
    level.
    """
    if connection.vendor == "postgresql":
        return
    if os.environ.get("REQUIRE_POSTGRES") == "1":
        pytest.fail(
            "REQUIRE_POSTGRES=1 but the active database is "
            f"{connection.vendor!r}. These tests are meaningless without PostgreSQL."
        )
    pytest.skip(f"concurrency semantics require PostgreSQL, not {connection.vendor!r}")


def _run_threads(count: int, work: Any) -> tuple[list[Any], list[BaseException]]:
    """Run ``work(index)`` in ``count`` threads released by one barrier.

    Returns:
        A pair of lists: the values returned by threads that completed, in the
        order they finished, and the exceptions raised by the rest. Nothing is
        swallowed — a thread that raises is recorded, not ignored.

    Raises:
        AssertionError: if a thread did not finish within ``TIMEOUT``. A
            deadlock or a missing row lock looks exactly like this.
    """
    barrier = threading.Barrier(count)
    results: list[Any] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def target(index: int) -> None:
        try:
            barrier.wait(timeout=TIMEOUT)
            value = work(index)
        except BaseException as exc:  # reported to the caller, never swallowed
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

    return results, errors


def run_concurrently(count: int, work: Any) -> list[Any]:
    """Run ``count`` callers and require that **none** of them raised.

    For races where the database decides a single winner and every other caller
    still returns cleanly — losing a conditional UPDATE returns "no rows
    matched", it does not raise.
    """
    results, errors = _run_threads(count, work)

    assert not errors, f"threads raised: {errors!r}"
    return results


def run_concurrently_with_losers(count: int, work: Any) -> tuple[list[Any], list[BaseException]]:
    """Run ``count`` callers where some are expected to lose by raising.

    For races whose losers fail at the row level — a unique constraint, or a
    conflict detected after the winner committed. The caller decides which
    exception types are acceptable; this only guarantees that every thread
    finished and that no exception was swallowed.
    """
    return _run_threads(count, work)
