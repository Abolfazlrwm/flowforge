"""Scheduler process: advance active runs and reclaim expired leases on an interval.

This is one of the three Phase 4 processes from ADR-011. It decides *which* tasks
dispatch (the worker, in Phase 5, decides who executes). It does not publish to the
broker itself — it writes outbox rows that the publisher process delivers.

The loop is intentionally boring: reclaim expired leases, advance every active run
once, sleep, repeat. ``--once`` exists so the same pass can be exercised by a test
without spawning a process that never returns.
"""

from __future__ import annotations

import signal
import threading
from argparse import ArgumentParser
from typing import Any, cast

from django.core.management.base import BaseCommand
from django.db import DatabaseError

from core.db import reset_connections
from core.domain.retry import CONNECTION_BACKOFF, calculate_backoff
from core.observability import metrics as obs_metrics
from core.observability import tracing
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
        parser.add_argument(
            "--metrics-port",
            type=obs_metrics.metrics_port_argument,
            default=0,
            # The custom type treats an empty string as "disabled": compose
            # interpolates an unset variable to an empty string, and a bare
            # `int("")` would kill the process before it ever started.
            help="Port for this process's Prometheus endpoint (0 or empty disables it).",
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

        tracing.configure_tracing("scheduler")
        metrics_port = int(opts["metrics_port"])
        if metrics_port:
            obs_metrics.start_metrics_server(metrics_port)
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())

        self.stdout.write("Scheduler started.")
        failures = 0
        while not stop.is_set():
            try:
                reaped, enqueued = schedule_once()
            except DatabaseError as exc:
                # A database restart kills the loop's one long-lived connection.
                # Throwing it away and backing off is what turns "the scheduler is
                # dead until someone restarts it" into "the scheduler resumes".
                reset_connections()
                failures += 1
                delay = calculate_backoff(attempt=failures, policy=CONNECTION_BACKOFF)
                self.stderr.write(
                    f"Scheduler pass failed ({exc}); retrying in {delay:.2f}s (attempt {failures})"
                )
                stop.wait(delay)
                continue

            if failures:
                self.stdout.write(f"Scheduler recovered after {failures} failed pass(es).")
                failures = 0
            if reaped or enqueued:
                self.stdout.write(
                    f"Pass complete: reaped {reaped} lease(s), enqueued {enqueued} dispatch(es)."
                )
            stop.wait(interval)
        self.stdout.write("Scheduler stopped.")
