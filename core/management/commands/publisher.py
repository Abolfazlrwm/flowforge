"""Publisher process: drain the transactional outbox to the broker on an interval.

This is the second of the three Phase 4 processes from ADR-011. The scheduler
wrote outbox rows; this process reads the pending ones (``FOR UPDATE SKIP LOCKED``
so concurrent publishers divide the queue), delivers each with publisher confirms,
and only then marks the row delivered. A crash before the confirm leaves the row
pending, so nothing is ever lost — at worst redelivered, and the worker
de-duplicates on ``dispatch_id``.

The loop is deliberately simple: publish one sweep, sleep, repeat. ``--once`` runs
a single sweep so a test can exercise it without a never-returning process.
"""

from __future__ import annotations

import time
from argparse import ArgumentParser
from typing import Any, cast

from django.conf import settings
from django.core.management.base import BaseCommand

from core.broker import build_publisher, publish_once


class Command(BaseCommand):
    """Run the publisher loop (or a single sweep with ``--once``)."""

    help = "Deliver pending transactional-outbox messages to the message broker."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the interval and one-shot flags."""
        parser.add_argument(
            "--interval",
            type=float,
            default=1.0,
            help="Seconds to sleep between sweeps (default: 1).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single sweep and exit, instead of looping forever.",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Run the loop, or a single sweep when ``--once`` is given."""
        opts = cast("dict[str, Any]", options)
        interval = float(opts["interval"])
        publisher = build_publisher(settings.RABBITMQ_URL)
        try:
            if bool(opts["once"]):
                published = publish_once(publisher)
                self.stdout.write(f"Published one sweep: {published} message(s) delivered.")
                return

            self.stdout.write("Publisher started.")
            try:
                while True:
                    published = publish_once(publisher)
                    if published:
                        self.stdout.write(f"Delivered {published} message(s).")
                    time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write("Publisher stopped.")
        finally:
            publisher.close()
