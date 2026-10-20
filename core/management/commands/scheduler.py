"""Scheduler process: advance active runs and reclaim expired leases on an interval.

This is one of the three Phase 4 processes from ADR-011. It decides *which* tasks
dispatch (the worker, in Phase 5, decides who executes). It does not publish to the
broker itself — it writes outbox rows that the publisher process delivers.

The loop is intentionally boring: reclaim expired leases, advance every active run
once, sleep, repeat. ``--once`` exists so the same pass can be exercised by a test
without spawning a process that never returns.
"""

from __future__ import annotations

import time
from argparse import ArgumentParser
from typing import Any, cast

from django.core.management.base import BaseCommand

from core.repositories.scheduler import schedule_once


class Command(BaseCommand):
    """Run the scheduler loop (or a single pass with ``--once``)."""

    help = "Schedule eligible tasks of active workflow runs and reclaim expired leases."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the interval and one-shot flags."""
        parser.add_argument(
            "--interval",
            type=float,
            default=5.0,
            help="Seconds to sleep between passes (default: 5).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single pass and exit, instead of looping forever.",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Run the loop, or a single pass when ``--once`` is given."""
        opts = cast("dict[str, Any]", options)
        interval = float(opts["interval"])
        if bool(opts["once"]):
            reaped, enqueued = schedule_once()
            self.stdout.write(
                f"Scheduled one pass: reaped {reaped} lease(s), enqueued {enqueued} dispatch(es)."
            )
            return

        self.stdout.write("Scheduler started.")
        try:
            while True:
                reaped, enqueued = schedule_once()
                if reaped or enqueued:
                    self.stdout.write(
                        "Pass complete: reaped "
                        f"{reaped} lease(s), enqueued {enqueued} dispatch(es)."
                    )
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("Scheduler stopped.")
