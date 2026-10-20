"""Worker process: consume task dispatches and execute them.

The handler registry is trusted and in-process: importing
:mod:`core.worker.builtin` populates it, and a dispatch message can only name a
``task_type`` that is already registered. Nothing in a message is imported or
executed as code.

SIGTERM (and SIGINT) stop consumption *after* the in-flight message is finished and
acked, so a rolling deploy does not abandon a half-done task. Unacked messages are
redelivered by the broker.
"""

from __future__ import annotations

import signal
from argparse import ArgumentParser
from typing import Any, cast

from django.conf import settings
from django.core.management.base import BaseCommand

from core.worker import builtin  # noqa: F401 - importing it populates the registry
from core.worker.consumer import DEFAULT_QUEUE, Worker


class Command(BaseCommand):
    """Consume dispatches, claim each task run, execute it, and ack."""

    help = "Consume task dispatches, claim each task run, execute it, and ack."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the queue, prefetch and lease options."""
        parser.add_argument(
            "--queue",
            default=DEFAULT_QUEUE,
            help=f"Queue to consume from (default: {DEFAULT_QUEUE}).",
        )
        parser.add_argument(
            "--prefetch",
            type=int,
            default=1,
            help="Max unacked messages per worker (default: 1).",
        )
        parser.add_argument(
            "--lease-seconds",
            type=int,
            default=30,
            help="Lease length; the heartbeat renews it every third (default: 30).",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Start the consumer and block until SIGTERM/SIGINT."""
        opts = cast("dict[str, Any]", options)
        worker = Worker(
            url=settings.RABBITMQ_URL,
            queue=str(opts["queue"]),
            prefetch=int(opts["prefetch"]),
            lease_seconds=int(opts["lease_seconds"]),
        )
        signal.signal(signal.SIGTERM, lambda *_: worker.stop())
        signal.signal(signal.SIGINT, lambda *_: worker.stop())
        self.stdout.write(f"Worker {worker.worker_id} started on queue {worker.queue}.")
        worker.run()
        self.stdout.write("Worker stopped.")
