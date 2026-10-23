"""Publish a sample workflow, trigger it, and wait for it to succeed.

The compose CI job runs this as the end-to-end check: it drives the real pipeline
(trigger -> scheduler -> outbox -> publisher -> RabbitMQ -> worker -> DB) and exits
non-zero unless the run reaches SUCCEEDED. It is a smoke test that the processes
agree with each other, not a unit test.
"""

from __future__ import annotations

import time
from argparse import ArgumentParser
from typing import Any, cast

from django.core.management.base import BaseCommand

from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.states import WorkflowRunState
from core.repositories.definitions import publish_definition
from core.services.runs import trigger
from core.services.tenancy import ensure_default_organization


class Command(BaseCommand):
    """Publish the sample workflow, trigger it, and wait for SUCCEEDED."""

    help = "Publish a sample workflow, trigger a run, and wait for it to succeed."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the timeout and poll interval."""
        parser.add_argument(
            "--timeout",
            type=float,
            default=120.0,
            help="Seconds to wait for the run to finish (default: 120).",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=1.0,
            help="Seconds between polls (default: 1).",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Run the sample end to end."""
        opts = cast("dict[str, Any]", options)
        timeout = float(opts["timeout"])
        interval = float(opts["interval"])

        definition = WorkflowDefinition(
            name="sample-e2e",
            version=1,
            description="The compose end-to-end sample.",
            tasks=(
                TaskDefinition(key="seed", task_type="echo", config={"value": "hello"}),
                TaskDefinition(
                    key="sum",
                    task_type="add",
                    depends_on=("seed",),
                    config={"values": [1, 2, 3]},
                ),
                TaskDefinition(key="done", task_type="noop", depends_on=("sum",)),
            ),
        )
        organization = ensure_default_organization()
        workflow, created = publish_definition(definition, organization=organization)
        self.stdout.write(f"Published sample-e2e (created={created}).")

        run, _ = trigger(workflow, requested_by_id=None, organization=organization)
        self.stdout.write(f"Triggered run {run.id}; waiting up to {timeout:.0f}s.")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run.refresh_from_db()
            if run.state == WorkflowRunState.SUCCEEDED.value:
                self.stdout.write(self.style.SUCCESS(f"Run {run.id} SUCCEEDED."))
                return
            if run.state in (
                WorkflowRunState.FAILED.value,
                WorkflowRunState.CANCELLED.value,
            ):
                raise SystemExit(f"Run {run.id} ended {run.state}.")
            time.sleep(interval)
        raise SystemExit(f"Run {run.id} did not finish within {timeout:.0f}s (state={run.state}).")
