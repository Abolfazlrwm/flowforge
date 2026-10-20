"""RabbitMQ publisher for FlowForge's transactional outbox.

Imported lazily by :func:`core.broker.build_publisher` for ``amqp://`` URLs, so the
rest of the codebase (and the test suite, which uses the fake publisher) never
imports pika. The contract is publisher confirms: a message is only considered
delivered once the broker acknowledges it, which is what lets :func:`publish_once`
mark the outbox row *after* a real delivery rather than before.

This module has not been exercised against a live broker in CI (no RabbitMQ there);
it is validated structurally and the delivery-confirmation path is the property the
worker's ``dispatch_id`` de-duplication relies on.
"""

from __future__ import annotations

import json
from typing import Any

import pika

#: The default exchange every dispatch is published to. Durable so a broker restart
#: between the outbox write and the worker read does not drop messages.
DEFAULT_EXCHANGE = "flowforge.direct"


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
            )
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
