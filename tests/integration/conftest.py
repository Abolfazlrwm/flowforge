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
import time
from collections.abc import Callable
from typing import Any

import pika
import pytest
from core.broker_rabbit import DEFAULT_EXCHANGE, RabbitPublisher, declare_dispatch_queue
from core.worker.consumer import DEFAULT_ROUTING_KEY
from django.conf import settings
from django.db import connection

THREADS = 16
TIMEOUT = 30.0


def wait_until(predicate: Callable[[], bool], timeout: float = 30.0) -> bool:
    """Poll ``predicate`` until it is true, or ``timeout`` elapses.

    A bounded poll, not a synchronisation sleep: it returns as soon as the
    condition holds, so a test that passes does so without waiting out the clock.
    Returns whether the predicate ever became true, leaving the assertion (and its
    message) to the caller.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def subprocess_database_url() -> str:
    """The *test* database URL, so a child process sees the rows this test created.

    A subprocess started with ``DJANGO_SETTINGS_MODULE=flowforge.settings.test``
    still reads ``DATABASE_URL`` from the environment, so it must be handed the
    test database explicitly or it would operate on a different one.
    """
    db = settings.DATABASES["default"]
    host = db.get("HOST") or "localhost"
    port = db.get("PORT") or "5432"
    return f"postgres://{db['USER']}:{db['PASSWORD']}@{host}:{port}/{db['NAME']}"


def bind_queue(url: str, queue: str) -> pika.BlockingConnection:
    """Declare and bind ``queue`` to the dispatch exchange before publishing.

    Declaring the queue first means a published message waits for the worker
    instead of racing the worker's own declaration (an unroutable message to a
    non-existent queue is silently dropped).
    """
    conn = pika.BlockingConnection(pika.URLParameters(url))
    channel = conn.channel()
    channel.exchange_declare(exchange=DEFAULT_EXCHANGE, exchange_type="direct", durable=True)
    declare_dispatch_queue(channel, queue)
    channel.queue_bind(queue=queue, exchange=DEFAULT_EXCHANGE, routing_key=DEFAULT_ROUTING_KEY)
    return conn


def publish_raw(url: str, payload: dict[str, object]) -> None:
    """Publish one dispatch payload directly, bypassing the outbox."""
    publisher = RabbitPublisher(url)
    try:
        publisher.publish(
            exchange=DEFAULT_EXCHANGE,
            routing_key=DEFAULT_ROUTING_KEY,
            payload=payload,
        )
    finally:
        publisher.close()


def queue_is_empty(url: str, queue: str) -> bool:
    """Whether ``queue`` currently holds no messages."""
    conn = pika.BlockingConnection(pika.URLParameters(url))
    try:
        result = conn.channel().queue_declare(queue=queue, passive=True)
        return result.method.message_count == 0
    finally:
        conn.close()


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


def require_rabbitmq() -> str:
    """Return ``RABBITMQ_URL``, or skip/fail when no broker is available.

    Locally without a broker these tests skip; in CI ``REQUIRE_RABBITMQ=1`` turns a
    missing or unreachable broker into a failure, so the publisher's delivery
    guarantees are never silently unexercised. The reachability probe opens and
    closes a real AMQP connection, so "the port is open" is not mistaken for "the
    broker works".
    """
    url = os.environ.get("RABBITMQ_URL")
    if not url:
        if os.environ.get("REQUIRE_RABBITMQ") == "1":
            pytest.fail("REQUIRE_RABBITMQ=1 but RABBITMQ_URL is not set.")
        pytest.skip("RABBITMQ_URL is not set; RabbitMQ integration tests need a broker.")
    try:
        connection = pika.BlockingConnection(pika.URLParameters(url))
        connection.close()
    except Exception as exc:
        if os.environ.get("REQUIRE_RABBITMQ") == "1":
            pytest.fail(f"REQUIRE_RABBITMQ=1 but RabbitMQ is unreachable at {url}: {exc}")
        pytest.skip(f"RabbitMQ unreachable at {url}: {exc}")
    return url


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
