"""RabbitMQ publisher for FlowForge's transactional outbox.

Imported lazily by :func:`core.broker.build_publisher` for ``amqp://`` URLs, so the
rest of the codebase (and the test suite, which uses the fake publisher) never
imports pika. The contract is publisher confirms: a message is only considered
delivered once the broker acknowledges it, which is what lets :func:`publish_once`
mark the outbox row *after* a real delivery rather than before.

Publishing is **mandatory**: if no queue is bound to the exchange/routing key, the
broker returns the message and pika raises ``UnroutableError``, which this class
surfaces as :class:`PublisherError`. That is what keeps an outbox row pending when
the worker's queue does not exist yet, instead of marking it delivered into the
void.

These behaviours are exercised against a real broker in
``tests/integration/test_rabbit_publisher.py`` (CI runs them with
``REQUIRE_RABBITMQ=1``).
"""

from __future__ import annotations

import json
from typing import Any

import pika

#: The default exchange every dispatch is published to. Durable so a broker restart
#: between the outbox write and the worker read does not drop messages.
DEFAULT_EXCHANGE = "flowforge.direct"

#: Arguments every declaration of the dispatch queue must carry.
#:
#: A **quorum queue** replicates each message through Raft and fsyncs it before
#: acknowledging, so a confirmed publish survives an abrupt broker failure — the
#: guarantee a classic durable queue does *not* give (it can lose a message the
#: broker confirmed but had not flushed). On a single node this buys crash-safe
#: durability, not high availability: there is no replica to fail over to.
#:
#: The type is fixed at declaration time. An existing classic queue with the same
#: name cannot be converted in place; see ``docs/architecture/execution-model.md``
#: for the delete-and-redeclare procedure, and :func:`declare_dispatch_queue` for
#: the error that tells an operator they need it.
DISPATCH_QUEUE_ARGUMENTS: dict[str, Any] = {"x-queue-type": "quorum"}


class QueueTypeMismatchError(RuntimeError):
    """The dispatch queue exists with a different type than this build declares.

    Raised instead of letting a raw ``PRECONDITION_FAILED`` channel closure
    surface, because the fix is a documented operator action rather than a retry:
    a queue's type cannot be changed in place.
    """


def declare_dispatch_queue(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    queue: str,
) -> None:
    """Declare the dispatch queue as a quorum queue, with a migration-shaped error.

    Every component that declares this queue must use these arguments. If the
    queue already exists as a classic queue, the broker closes the channel with
    ``PRECONDITION_FAILED``; that is translated into
    :class:`QueueTypeMismatchError` carrying the procedure, because retrying will
    never fix it and the operator needs to know why.

    Raises:
        QueueTypeMismatchError: if the queue exists with a different type.
    """
    try:
        channel.queue_declare(
            queue=queue,
            durable=True,
            arguments=DISPATCH_QUEUE_ARGUMENTS,
        )
    except pika.exceptions.ChannelClosedByBroker as exc:
        if exc.reply_code != 406:  # PRECONDITION_FAILED
            raise
        raise QueueTypeMismatchError(
            f"The queue {queue!r} already exists with a different type than "
            f"{DISPATCH_QUEUE_ARGUMENTS['x-queue-type']!r}. A queue's type cannot be "
            "changed in place. Drain it, then delete and redeclare:\n"
            f"  1. stop the worker and publisher;\n"
            f"  2. wait until the queue is empty (or accept losing unacked dispatches, "
            f"which the outbox will republish);\n"
            f"  3. rabbitmqctl delete_queue {queue}  (or the management UI);\n"
            f"  4. start the worker, which redeclares it as a quorum queue;\n"
            f"  5. start the publisher.\n"
            "See docs/architecture/execution-model.md."
        ) from exc


class RabbitPublisher:
    """Delivers outbox payloads to RabbitMQ, blocking on publisher confirms."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.adapters.blocking_connection.BlockingChannel | None = None

    def _connect(self) -> pika.adapters.blocking_connection.BlockingChannel:
        """Open the connection, channel, and declare the durable exchange once."""
        params = pika.URLParameters(self._url)
        connection = pika.BlockingConnection(params)
        self._connection = connection
        channel = connection.channel()
        channel.confirm_delivery()
        channel.exchange_declare(
            exchange=DEFAULT_EXCHANGE,
            exchange_type="direct",
            durable=True,
        )
        self._channel = channel
        return channel

    def _get_channel(self) -> pika.adapters.blocking_connection.BlockingChannel:
        """Return a live channel, (re)opening the connection if needed."""
        if self._channel is None or not self._channel.is_open:
            return self._connect()
        return self._channel

    def publish(self, *, exchange: str, routing_key: str, payload: dict[str, Any]) -> None:
        """Publish ``payload`` and block until the broker confirms receipt.

        Raises on a broker nack or a missing confirmation, which (in
        :func:`core.broker.publish_once`) rolls back the outbox transaction so the
        row stays pending and a later sweep redelivers it.
        """
        channel = self._get_channel()
        try:
            confirmed = channel.basic_publish(
                exchange=exchange,
                routing_key=routing_key,
                body=json.dumps(payload),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # persistent — survive a broker restart
                    content_type="application/json",
                ),
                # mandatory: an unroutable message is returned, not silently
                # dropped, so the outbox row stays pending until a queue exists.
                mandatory=True,
            )
        except pika.exceptions.UnroutableError as exc:
            raise PublisherError("message was unroutable (no queue bound to the exchange)") from exc
        except pika.exceptions.NackError as exc:
            raise PublisherError("broker rejected the message") from exc
        if confirmed is False:
            raise PublisherError("broker did not confirm message delivery")

    def close(self) -> None:
        """Close the channel and connection if open."""
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None


class PublisherError(RuntimeError):
    """Raised when the broker does not confirm a published message."""
