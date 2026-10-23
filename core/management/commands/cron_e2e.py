"""Publish a workflow, schedule it, and wait for the *scheduler* to run it.

The compose CI job runs this as the cron end-to-end check. Unlike ``sample_run``,
nothing here triggers the run directly: a ``ScheduledTrigger`` is created and the
running scheduler process fires it on its next pass, so the trigger -> run ->
outbox -> publisher -> RabbitMQ -> worker -> SUCCEEDED path is driven entirely by
the real processes talking to each other. It exits non-zero unless a run of the
scheduled workflow reaches SUCCEEDED.
"""

from __future__ import annotations

import time
from argparse import ArgumentParser
from typing import Any, cast

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.states import WorkflowRunState
from core.models import ScheduledTrigger, WorkflowRun
from core.repositories.definitions import publish_definition
from core.services.tenancy import ensure_default_organization


class Command(BaseCommand):
    """Schedule a workflow and wait for the scheduler to run it to SUCCEEDED."""

    help = "Schedule a workflow and wait for the scheduler to run it to SUCCEEDED."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the timeout and poll interval."""
        parser.add_argument(
            "--timeout",
            type=float,
            default=120.0,
            help="Seconds to wait for the triggered run to finish (default: 120).",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=1.0,
            help="Seconds between polls (default: 1).",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Publish, schedule, and wait for the scheduler-driven run to succeed."""
        opts = cast("dict[str, Any]", options)
        timeout = float(opts["timeout"])
        interval = float(opts["interval"])

        definition = WorkflowDefinition(
            name="cron-e2e",
            version=1,
            description="The compose cron end-to-end sample.",
            tasks=(
                TaskDefinition(key="seed", task_type="echo", config={"value": "cron"}),
                TaskDefinition(key="done", task_type="noop", depends_on=("seed",)),
            ),
        )
        organization = ensure_default_organization()
        workflow, created = publish_definition(definition, organization=organization)
        self.stdout.write(f"Published cron-e2e (created={created}).")

        # Every minute, due immediately: the scheduler's next pass fires it.
        trigger = ScheduledTrigger.objects.create(
            organization=organization,
            workflow=workflow,
            cron_expression="* * * * *",
            enabled=True,
            next_run_at=timezone.now(),
        )
        self.stdout.write(
            f"Scheduled trigger {trigger.id}; waiting up to {timeout:.0f}s for the "
            "scheduler to fire it."
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = WorkflowRun.objects.filter(workflow=workflow).order_by("-created_at").first()
            if run is not None:
                self.stdout.write(f"run {run.id} state={run.state}")
                if run.state == WorkflowRunState.SUCCEEDED.value:
                    self.stdout.write(self.style.SUCCESS(f"Cron-triggered run {run.id} SUCCEEDED."))
                    return
                if run.state in (
                    WorkflowRunState.FAILED.value,
                    WorkflowRunState.CANCELLED.value,
                ):
                    raise SystemExit(f"Cron-triggered run {run.id} ended {run.state}.")
            time.sleep(interval)

        raise SystemExit(
            f"No cron-triggered run of {workflow.name} reached SUCCEEDED within {timeout:.0f}s."
        )
