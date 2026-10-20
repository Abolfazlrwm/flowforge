"""The worker against a real broker and PostgreSQL.

These prove the properties a fake cannot: the broker redelivers a message whose ack
was lost, a duplicate delivery is handled once, and a graceful stop finishes the
in-flight message before exiting. CI runs them with ``REQUIRE_RABBITMQ=1`` and
``REQUIRE_POSTGRES=1``.

No arbitrary sleeps: waits poll a predicate with a deadline.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import pika
import pytest
from core.broker_rabbit import DEFAULT_EXCHANGE, RabbitPublisher
from core.domain.states import TaskRunState
from core.models import TaskRun
from core.worker.consumer import DEFAULT_ROUTING_KEY, Worker
from core.worker.registry import register
from django.db import connection

from tests.integration.conftest import require_postgres, require_rabbitmq

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]

_COUNTS: dict[str, int] = {}
_SLOW_STARTED = threading.Event()
_SLOW_RELEASE = threading.Event()


def _count(config: Mapping[str, Any]) -> None:
    key = str(config.get("key", "default"))
    _COUNTS[key] = _COUNTS.get(key, 0) + 1


def _slow(_config: Mapping[str, Any]) -> None:
    _SLOW_STARTED.set()
    _SLOW_RELEASE.wait(timeout=10)


register("test.count", _count)
register("test.slow", _slow)


@pytest.fixture(autouse=True)
def _reset() -> None:
    _COUNTS.clear()
    _SLOW_STARTED.clear()
    _SLOW_RELEASE.clear()


def _payload(task_run: TaskRun, task_type: str, *, key: str = "default") -> dict[str, Any]:
    return {
        "dispatch_id": uuid.uuid4().hex,
        "task_run_id": str(task_run.id),
        "task_type": task_type,
        "config": {"key": key},
        "fencing_token": task_run.fencing_token,
    }


def _bind_queue(url: str, queue: str) -> pika.BlockingConnection:
    """Declare the worker's queue and bind it, with the same args the Worker uses."""
    conn = pika.BlockingConnection(pika.URLParameters(url))
    channel = conn.channel()
    channel.exchange_declare(exchange=DEFAULT_EXCHANGE, exchange_type="direct", durable=True)
    channel.queue_declare(queue=queue, durable=True)
    channel.queue_bind(queue=queue, exchange=DEFAULT_EXCHANGE, routing_key=DEFAULT_ROUTING_KEY)
    return conn


def _publish(url: str, payload: dict[str, Any]) -> None:
    publisher = RabbitPublisher(url)
    try:
        publisher.publish(
            exchange=DEFAULT_EXCHANGE,
            routing_key=DEFAULT_ROUTING_KEY,
            payload=payload,
        )
    finally:
        publisher.close()


def _wait_until(predicate: Callable[[], bool], timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)  # bounded poll, not a synchronisation sleep
    return False


def _state(task_run: TaskRun) -> str:
    task_run.refresh_from_db()
    return task_run.state


def _queue_is_empty(url: str, queue: str) -> bool:
    conn = pika.BlockingConnection(pika.URLParameters(url))
    try:
        result = conn.channel().queue_declare(queue=queue, passive=True)
        return result.method.message_count == 0
    finally:
        conn.close()


def _start_worker(url: str, queue: str) -> tuple[Worker, threading.Thread]:
    worker = Worker(url=url, queue=queue, lease_seconds=30, worker_id="w-test")

    def run() -> None:
        try:
            worker.run()
        finally:
            connection.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return worker, thread


def _stop_worker(
    worker: Worker, thread: threading.Thread, conn: pika.BlockingConnection, queue: str
) -> None:
    worker.stop()
    thread.join(timeout=15)
    try:
        conn.channel().queue_delete(queue=queue)
    finally:
        conn.close()


def test_worker_consumes_a_dispatch_end_to_end(task_run: TaskRun) -> None:
    """A published dispatch is claimed, executed, finished and acked."""
    require_postgres()
    url = require_rabbitmq()
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    conn = _bind_queue(url, queue)
    worker, thread = _start_worker(url, queue)
    try:
        _publish(url, _payload(task_run, "test.count", key="e2e"))
        assert _wait_until(lambda: _state(task_run) == TaskRunState.SUCCEEDED.value)
        assert _COUNTS.get("e2e") == 1
    finally:
        _stop_worker(worker, thread, conn, queue)


def test_duplicate_delivery_runs_the_handler_once(task_run: TaskRun) -> None:
    """The same message delivered twice executes once; the second is acked as a no-op."""
    require_postgres()
    url = require_rabbitmq()
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    conn = _bind_queue(url, queue)
    worker, thread = _start_worker(url, queue)
    try:
        payload = _payload(task_run, "test.count", key="dup")
        _publish(url, payload)
        _publish(url, payload)
        assert _wait_until(lambda: _state(task_run) == TaskRunState.SUCCEEDED.value)
        # Both messages are consumed (the queue drains) but the handler ran once.
        assert _wait_until(lambda: _queue_is_empty(url, queue))
        assert _COUNTS.get("dup") == 1
    finally:
        _stop_worker(worker, thread, conn, queue)


def test_lost_ack_redelivers_the_message(task_run: TaskRun) -> None:
    """A message consumed without an ack is redelivered after the connection closes."""
    require_postgres()
    url = require_rabbitmq()
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    conn = _bind_queue(url, queue)
    try:
        _publish(url, _payload(task_run, "test.count", key="lost"))
        # Consume without ack, then drop the connection: the broker requeues it.
        method, _properties, _body = conn.channel().basic_get(queue=queue, auto_ack=False)
        assert method is not None
        conn.close()

        conn2 = _bind_queue(url, queue)
        try:
            channel = conn2.channel()
            redelivered: dict[str, Any] | None = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                method2, _props2, body2 = channel.basic_get(queue=queue, auto_ack=True)
                if method2 is not None:
                    redelivered = json.loads(body2)
                    break
                conn2.process_data_events(time_limit=0.1)
            assert redelivered is not None
            assert redelivered["task_run_id"] == str(task_run.id)
        finally:
            conn2.channel().queue_delete(queue=queue)
            conn2.close()
    finally:
        if not conn.is_closed:
            conn.close()


def test_graceful_shutdown_finishes_the_in_flight_message(task_run: TaskRun) -> None:
    """stop() lets the running handler finish and ack before the worker exits."""
    require_postgres()
    url = require_rabbitmq()
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    conn = _bind_queue(url, queue)
    worker, thread = _start_worker(url, queue)
    try:
        _publish(url, _payload(task_run, "test.slow", key="slow"))
        assert _SLOW_STARTED.wait(timeout=15)

        worker.stop()  # asked to stop while the handler is still running
        _SLOW_RELEASE.set()  # let the handler return
        thread.join(timeout=15)

        assert not thread.is_alive()
        # The in-flight message finished and was acked, not abandoned.
        assert _state(task_run) == TaskRunState.SUCCEEDED.value
    finally:
        _stop_worker(worker, thread, conn, queue)
