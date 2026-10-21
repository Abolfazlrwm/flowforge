"""The publisher: the outbox drain loop and the broker factory.

These run on SQLite — the publisher's job is pure database coordination (which rows
are pending, lock them, mark them done) plus a call into a broker abstraction that
the fake keeps in memory. The SKIP LOCKED concurrency behaviour is covered
separately in the integration suite against PostgreSQL.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pika
import pytest
from core.broker import FakePublisher, build_publisher, publish_once
from core.broker_rabbit import QueueTypeMismatchError, declare_dispatch_queue
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import OutboxMessage, Task, TaskRun, Workflow, WorkflowRun
from core.repositories import enqueue_ready_tasks
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

pytestmark = pytest.mark.django_db


def _pending_outbox(name: str, count: int) -> WorkflowRun:
    """Create a run with ``count`` READY task runs and one pending outbox row each."""
    workflow = Workflow.objects.create(name=name, version=1)
    run = WorkflowRun.objects.create(workflow=workflow, state=WorkflowRunState.PENDING.value)
    for i in range(count):
        task = Task.objects.create(workflow=workflow, key=f"t{i}", task_type="noop")
        TaskRun.objects.create(workflow_run=run, task=task, state=TaskRunState.READY.value)
    enqueue_ready_tasks(run)
    return run


def test_publish_once_delivers_every_pending_row() -> None:
    """A sweep drains all pending rows and stamps each delivered."""
    _pending_outbox("pub-all", 3)
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 3

    publisher = FakePublisher()
    delivered = publish_once(publisher)

    assert delivered == 3
    assert len(publisher.sent) == 3
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0
    # The payloads carry the task identity the worker needs.
    keys = {message["payload"]["task_key"] for message in publisher.sent}
    assert keys == {f"t{i}" for i in range(3)}
    assert all(m["routing_key"] == "task.dispatch" for m in publisher.sent)


def test_publish_once_second_sweep_delivers_nothing() -> None:
    """Once a row is delivered it is no longer pending, so a re-sweep is a no-op."""
    _pending_outbox("pub-idem", 2)
    assert publish_once(FakePublisher()) == 2

    again = FakePublisher()
    assert publish_once(again) == 0
    assert again.sent == []


def test_publish_once_respects_limit() -> None:
    """``limit`` bounds one sweep; a later sweep finishes the rest."""
    _pending_outbox("pub-limit", 5)

    first = FakePublisher()
    assert publish_once(first, limit=2) == 2
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 3

    second = FakePublisher()
    assert publish_once(second, limit=10) == 3
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0


def test_build_publisher_fake_returns_fake() -> None:
    """The ``fake://`` scheme selects the in-memory publisher for tests/dry runs."""
    publisher = build_publisher("fake://")
    assert isinstance(publisher, FakePublisher)
    publisher.close()  # must be safe to call even though there is nothing to release


def test_build_publisher_rejects_unknown_scheme() -> None:
    """A non-broker URL is a configuration error, not a silent fallback."""
    with pytest.raises(ValueError):
        build_publisher("http://localhost:5672")


def test_configured_rabbitmq_url_uses_the_root_vhost() -> None:
    """Regression: `//` parses as an empty vhost; the configured URL must be `/`.

    A trailing `//` made every AMQP handshake fail with "vhost not found" in CI,
    which the old TCP-only readiness check had hidden.
    """
    params = pika.URLParameters(settings.RABBITMQ_URL)
    assert params.virtual_host == "/"


def test_build_publisher_rabbit_when_pika_available() -> None:
    """``amqp://`` selects the RabbitMQ publisher (only if pika is installed)."""
    pytest.importorskip("pika")
    from core.broker_rabbit import RabbitPublisher  # noqa: PLC0415

    publisher = build_publisher("amqp://guest:guest@localhost:5672/")
    assert isinstance(publisher, RabbitPublisher)
    publisher.close()


def test_publisher_command_single_pass() -> None:
    """`manage.py publisher --once` drains pending rows in a single sweep."""
    _pending_outbox("pub-cmd", 2)
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 2

    # Point the command at the in-memory publisher so no broker is required.
    with override_settings(RABBITMQ_URL="fake://"):
        call_command("publisher", "--once")

    # Every row was delivered and stamped; the fake publisher is dropped, but the
    # durable effect (no pending rows remain) is what the command must guarantee.
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0


class TestQuorumQueueDeclaration:
    """The dispatch queue is a quorum queue, and a type mismatch is explained."""

    def test_the_queue_is_declared_as_a_quorum_queue(self) -> None:
        """Quorum is what makes a confirmed publish survive an abrupt broker failure."""
        channel = MagicMock()

        declare_dispatch_queue(channel, "flowforge.tasks")

        channel.queue_declare.assert_called_once_with(
            queue="flowforge.tasks",
            durable=True,
            arguments={"x-queue-type": "quorum"},
        )

    def test_an_existing_classic_queue_raises_a_migration_error(self) -> None:
        """PRECONDITION_FAILED is an operator action, so it must say what to do."""
        channel = MagicMock()
        channel.queue_declare.side_effect = pika.exceptions.ChannelClosedByBroker(
            406, "PRECONDITION_FAILED - inequivalent arg 'x-queue-type'"
        )

        with pytest.raises(QueueTypeMismatchError) as raised:
            declare_dispatch_queue(channel, "flowforge.tasks")

        message = str(raised.value)
        assert "delete_queue flowforge.tasks" in message
        assert "cannot be changed in place" in message

    def test_another_channel_error_is_not_disguised_as_a_type_mismatch(self) -> None:
        """Only 406 means "wrong type"; anything else is a different problem."""
        channel = MagicMock()
        channel.queue_declare.side_effect = pika.exceptions.ChannelClosedByBroker(404, "NOT_FOUND")

        with pytest.raises(pika.exceptions.ChannelClosedByBroker):
            declare_dispatch_queue(channel, "flowforge.tasks")
