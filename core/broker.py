"""The publisher's broker-agnostic half: what a publisher must do, and the loop.

This module never imports pika (or any AMQP client). It defines the
:class:`Publisher` contract, an in-memory :class:`FakePublisher` the unit and
integration tests drive, and :func:`publish_once` — the body of one publisher
sweep. The RabbitMQ implementation lives in :mod:`core.broker_rabbit` and is
imported lazily by :func:`build_publisher` so this module (and the fake path, and
the test suite) never pull pika into the import graph.

The reliability contract is the transactional outbox: a row is only marked
delivered *after* the broker confirms receipt. A crash before that point leaves
the row pending, a later sweep redelivers it, and the worker de-duplicates on
``dispatch_id`` — so a redelivery is harmless, never a duplicate execution.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.db import transaction

from core.observability import metrics as obs_metrics
from core.observability import tracing
from core.repositories.outbox import fetch_pending, mark_published

#: The queue the publisher feeds and the worker drains. Kept here (no pika) so
#: the broker-agnostic module and the metrics probe can name it.
DISPATCH_QUEUE = "flowforge.tasks"


class Publisher(Protocol):
    """A thing that can deliver a serialised dispatch to the message broker."""

    def publish(self, *, exchange: str, routing_key: str, payload: dict[str, Any]) -> None:
        """Deliver ``payload`` to ``exchange`` under ``routing_key``.

        Must block until the broker has confirmed receipt (publisher confirms), so
        the caller can mark the outbox row delivered. Raising here leaves the row
        pending and therefore redeliverable.
        """
        ...

    def queue_depth(self, queue: str) -> int:
        """Return the broker's current message count for ``queue``.

        A metrics probe, not part of delivery: a publisher whose broker does not
        support a passive declare may raise, and the caller treats that as "no
        reading" rather than as a failed sweep.
        """
        ...

    def close(self) -> None:
        """Release any broker connection. Safe to call whether or not it was used."""
        ...


@dataclass
class FakePublisher:
    """An in-memory publisher for tests and local dry runs.

    Every ``publish`` call is recorded in ``sent`` so a test can assert on exactly
    what would have been delivered, without standing up a broker.
    """

    sent: list[dict[str, Any]] = field(default_factory=list)

    def publish(self, *, exchange: str, routing_key: str, payload: dict[str, Any]) -> None:
        """Record the delivery instead of sending it."""
        self.sent.append({"exchange": exchange, "routing_key": routing_key, "payload": payload})

    def queue_depth(self, _queue: str) -> int:
        """An in-memory publisher has no broker queue; report zero."""
        return 0

    def close(self) -> None:
        """Nothing to release for an in-memory publisher."""
        return None


def publish_once(publisher: Publisher, *, limit: int = 1000) -> int:
    """Deliver every pending outbox row this sweep can lock, exactly once each.

    Runs inside one transaction: ``fetch_pending`` locks the rows with ``FOR UPDATE
    SKIP LOCKED`` so a peer publisher cannot take them, each is handed to the
    broker (which blocks on publisher confirms), and then ``mark_published`` stamps
    the row. Because the stamp only happens after the broker confirms, a crash
    mid-sweep leaves the row pending and a later sweep redelivers it; the worker's
    ``dispatch_id`` de-duplication makes the redelivery safe.

    Returns the number of rows marked delivered.
    """
    published = 0
    with transaction.atomic():
        for message in fetch_pending(limit=limit):
            # The publisher's span is a child of the scheduler's dispatch span;
            # its traceparent replaces the one in the *published* payload (the
            # stored row is unchanged), so the worker continues the publisher's
            # span and the chain API -> scheduler -> publisher -> worker shares
            # one trace id.
            with tracing.start_span(
                "outbox.publish",
                context=tracing.context_from_traceparent(message.traceparent),
                attributes={
                    "flowforge.dispatch_id": str(message.id),
                    "flowforge.organization_id": str(message.organization_id or ""),
                },
            ):
                payload = dict(message.payload)
                payload["traceparent"] = tracing.current_traceparent() or message.traceparent
                publisher.publish(
                    exchange=message.exchange,
                    routing_key=message.routing_key,
                    payload=payload,
                )
            if mark_published(message.id):
                published += 1
    # Outside the transaction: a probe failure must not roll back a confirmed
    # publish, and an unsupported passive declare is not a delivery failure.
    with contextlib.suppress(Exception):
        obs_metrics.set_queue_depth(DISPATCH_QUEUE, publisher.queue_depth(DISPATCH_QUEUE))
    return published


def build_publisher(url: str) -> Publisher:
    """Construct the publisher for ``url``.

    ``amqp://`` / ``amqps://`` → the RabbitMQ publisher (pika, with publisher
    confirms). ``fake://`` / ``memory://`` → :class:`FakePublisher` for tests and
    dry runs. Anything else is a configuration error, not a silent fallback.
    """
    if url.startswith(("fake://", "memory://", "fake", "memory")):
        return FakePublisher()
    if url.startswith(("amqp://", "amqps://")):
        from core.broker_rabbit import RabbitPublisher

        return RabbitPublisher(url)
    raise ValueError(f"Unsupported broker URL scheme in {url!r}; expected amqp:// or fake://.")
