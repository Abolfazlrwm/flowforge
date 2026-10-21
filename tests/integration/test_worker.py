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
from core.broker_rabbit import DEFAULT_EXCHANGE, RabbitPublisher, declare_dispatch_queue
from core.domain.states import TaskRunState
from core.models import Task, TaskRun
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
    _COUNTS["slow"] = _COUNTS.get("slow", 0) + 1
    _SLOW_STARTED.set()
    _SLOW_RELEASE.wait(timeout=10)


register("test.integration.count", _count)
register("test.integration.slow", _slow)


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
    # Same declaration the worker makes: a classic queue here would make the
    # worker's quorum declaration fail with PRECONDITION_FAILED.
    declare_dispatch_queue(channel, queue)
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
        _publish(url, _payload(task_run, "test.integration.count", key="e2e"))
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
        payload = _payload(task_run, "test.integration.count", key="dup")
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
        _publish(url, _payload(task_run, "test.integration.count", key="lost"))
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
        _publish(url, _payload(task_run, "test.integration.slow", key="slow"))
        assert _SLOW_STARTED.wait(timeout=15)

        worker.stop()  # asked to stop while the handler is still running
        _SLOW_RELEASE.set()  # let the handler return
        thread.join(timeout=15)

        assert not thread.is_alive()
        # The in-flight message finished and was acked, not abandoned.
        assert _state(task_run) == TaskRunState.SUCCEEDED.value
    finally:
        _stop_worker(worker, thread, conn, queue)


def test_a_dropped_connection_redelivers_and_completes_exactly_once(
    task_run: TaskRun, workflow: Any
) -> None:
    """Kill the consumer's connection mid-task: the broker redelivers, the task runs once.

    This is the "lost ack" failure at the connection level. The handler finishes and
    the durable write succeeds, but the ack cannot be delivered because the channel
    is gone. The worker must treat that as *not done* (so the broker requeues the
    message), reconnect on its own, and deduplicate the redelivery against the row
    it already finished.
    """
    require_postgres()
    url = require_rabbitmq()
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    conn = _bind_queue(url, queue)
    worker, thread = _start_worker(url, queue)
    try:
        _publish(url, _payload(task_run, "test.integration.slow", key="drop"))
        assert _SLOW_STARTED.wait(timeout=15), "the handler never started"

        # Closing from another thread is only safe through the connection's own
        # IOLoop; this is the documented way to touch a BlockingConnection.
        first_connection = worker._connection
        assert first_connection is not None
        first_connection.add_callback_threadsafe(first_connection.close)

        _SLOW_RELEASE.set()
        assert _wait_until(lambda: _state(task_run) == TaskRunState.SUCCEEDED.value, timeout=20), (
            "the task never completed after the connection was dropped"
        )

        # The worker reconnected by itself — no restart, no test-side nudge.
        assert _wait_until(
            lambda: worker._connection is not None and worker._connection is not first_connection,
            timeout=20,
        ), "the worker did not reconnect"
        assert thread.is_alive(), "the worker process must survive a lost connection"

        # The redelivery must not re-execute the handler: the row was already done.
        assert _COUNTS.get("slow", 0) == 1, f"handler ran {_COUNTS.get('slow', 0)} time(s)"

        # And it is consuming again: a fresh dispatch completes on the same worker.
        second_task = Task.objects.create(
            workflow=workflow, key="after-drop", task_type="test.integration.count"
        )
        second_run = TaskRun.objects.create(
            workflow_run=task_run.workflow_run,
            task=second_task,
            state=TaskRunState.READY.value,
        )
        _publish(url, _payload(second_run, "test.integration.count", key="after-drop"))
        assert _wait_until(
            lambda: _state(second_run) == TaskRunState.SUCCEEDED.value, timeout=20
        ), "the worker did not resume consuming after reconnecting"
    finally:
        _stop_worker(worker, thread, conn, queue)
