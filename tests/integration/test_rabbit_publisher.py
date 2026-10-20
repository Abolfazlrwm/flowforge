"""RabbitPublisher against a real broker.

These are the only tests that prove the publisher actually delivers: publisher
confirms, mandatory routing, outage behaviour and crash recovery are all broker
semantics that a fake cannot exercise. CI runs them with ``REQUIRE_RABBITMQ=1``
against a RabbitMQ service container, so a missing broker is a failure rather than
a silent skip.

No arbitrary sleeps: the consumer polls ``basic_get`` with a deadline, and the
outage test points at a dead port instead of pausing the shared broker.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pika
import pytest
from core.broker import publish_once
from core.broker_rabbit import DEFAULT_EXCHANGE, PublisherError, RabbitPublisher
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import OutboxMessage, Task, TaskRun, Workflow, WorkflowRun
from core.repositories import enqueue_ready_tasks

from tests.integration.conftest import require_rabbitmq, run_concurrently

pytestmark = [
    pytest.mark.integration,
    # transaction=True commits the seeded outbox rows so a separate publisher
    # connection (or thread) can see them, and lets SKIP LOCKED run for real.
    pytest.mark.django_db(transaction=True),
]


def _seed_outbox(name: str, count: int) -> list[OutboxMessage]:
    """Create ``count`` pending outbox rows through the production path."""
    workflow = Workflow.objects.create(name=name, version=1)
    run = WorkflowRun.objects.create(workflow=workflow, state=WorkflowRunState.PENDING.value)
    for i in range(count):
        task = Task.objects.create(workflow=workflow, key=f"t{i}", task_type="noop")
        TaskRun.objects.create(workflow_run=run, task=task, state=TaskRunState.READY.value)
    enqueue_ready_tasks(run)
    return list(OutboxMessage.objects.filter(workflow_run=run).order_by("created_at"))


def _bind_queue(
    url: str, routing_key: str = "task.dispatch"
) -> tuple[pika.BlockingConnection, str]:
    """Declare the exchange and bind a throwaway queue to it.

    The connection is returned and must stay open: closing it deletes the
    ``auto_delete`` queue, and the test needs the binding to outlive the publish.
    """
    connection = pika.BlockingConnection(pika.URLParameters(url))
    channel = connection.channel()
    channel.exchange_declare(exchange=DEFAULT_EXCHANGE, exchange_type="direct", durable=True)
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    channel.queue_declare(queue=queue, durable=False, auto_delete=True)
    channel.queue_bind(queue=queue, exchange=DEFAULT_EXCHANGE, routing_key=routing_key)
    return connection, queue


def _consume(
    connection: pika.BlockingConnection, queue: str, expected: int, timeout: float = 15.0
) -> list[dict[str, Any]]:
    """Poll ``basic_get`` until ``expected`` messages arrive or the deadline passes."""
    channel = connection.channel()
    messages: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while len(messages) < expected and time.monotonic() < deadline:
        method, _properties, body = channel.basic_get(queue=queue, auto_ack=True)
        if method is None:
            # Bounded wait for broker I/O; not a fixed sleep.
            connection.process_data_events(time_limit=0.1)
            continue
        messages.append(json.loads(body))
    return messages


def test_published_message_arrives_and_the_row_is_marked_delivered() -> None:
    """Publisher confirms: the payload reaches a bound queue and the row is stamped."""
    url = require_rabbitmq()
    connection, queue = _bind_queue(url)
    try:
        _seed_outbox("arrive", 1)
        publisher = RabbitPublisher(url)
        try:
            assert publish_once(publisher) == 1
        finally:
            publisher.close()

        messages = _consume(connection, queue, 1)
        assert len(messages) == 1
        payload = messages[0]
        assert payload["task_key"] == "t0"
        assert payload["dispatch_id"]
        assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0
    finally:
        connection.close()


def test_unroutable_publish_leaves_the_row_pending() -> None:
    """A publish with no bound queue is returned, so the row stays pending."""
    url = require_rabbitmq()
    rows = _seed_outbox("unroutable", 1)
    # Point at a routing key nothing is bound to: mandatory publish is returned.
    rows[0].routing_key = f"unroutable.{uuid.uuid4().hex}"
    rows[0].save(update_fields=["routing_key"])

    publisher = RabbitPublisher(url)
    try:
        with pytest.raises(PublisherError):
            publish_once(publisher)
    finally:
        publisher.close()

    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 1


def test_outage_leaves_rows_pending_then_recovery_drains_them_once() -> None:
    """A dead broker leaves rows pending; a live one drains them exactly once."""
    url = require_rabbitmq()
    _seed_outbox("outage", 3)

    # A dead port: the connection fails before any publish, so the transaction
    # rolls back and every row stays pending.
    dead = RabbitPublisher("amqp://guest:guest@127.0.0.1:1//")
    try:
        with pytest.raises(pika.exceptions.AMQPError):
            publish_once(dead)
    finally:
        dead.close()
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 3

    # Recovery: the live broker drains all three, exactly once.
    connection, queue = _bind_queue(url)
    try:
        publisher = RabbitPublisher(url)
        try:
            assert publish_once(publisher) == 3
        finally:
            publisher.close()
        messages = _consume(connection, queue, 3)
        assert len(messages) == 3
        assert len({m["dispatch_id"] for m in messages}) == 3
    finally:
        connection.close()
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0


def test_a_committed_row_is_delivered_by_a_fresh_publisher() -> None:
    """Crash window: a row committed before any publish is picked up by a new publisher."""
    url = require_rabbitmq()
    _seed_outbox("crash", 1)
    # The row is committed and no publisher has run: this is the crash window.
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 1

    connection, queue = _bind_queue(url)
    try:
        publisher = RabbitPublisher(url)
        try:
            assert publish_once(publisher) == 1
        finally:
            publisher.close()
        messages = _consume(connection, queue, 1)
        assert len(messages) == 1
    finally:
        connection.close()
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0


@pytest.mark.concurrency
def test_two_publishers_publish_each_row_exactly_once() -> None:
    """Two publishers divide the queue via SKIP LOCKED; no row is published twice."""
    url = require_rabbitmq()
    _seed_outbox("parallel", 20)
    connection, queue = _bind_queue(url)

    def sweep(_index: int) -> int:
        publisher = RabbitPublisher(url)
        try:
            return publish_once(publisher)
        finally:
            publisher.close()

    try:
        run_concurrently(2, sweep)
        messages = _consume(connection, queue, 20)
        assert len(messages) == 20
        assert len({m["dispatch_id"] for m in messages}) == 20
    finally:
        connection.close()
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0
