"""Drive a saga end to end: a task fails and the compensator really runs.

The compose CI job runs this as the saga end-to-end check. It publishes a
definition where ``charge`` fails (via the ``fail`` handler, so the failure is
produced by the real worker, not forced by a test) and ``undo_reserve`` compensates
the succeeded ``reserve``. It asserts the run reaches its final state ``FAILED``
*and* that ``undo_reserve`` actually executed to ``SUCCEEDED`` through the real
pipeline (scheduler -> outbox -> publisher -> RabbitMQ -> worker). It exits
non-zero otherwise.
"""

from __future__ import annotations

import time
from argparse import ArgumentParser
from typing import Any, cast

from django.core.management.base import BaseCommand

from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import TaskRun
from core.repositories.definitions import publish_definition
from core.services.runs import trigger


class Command(BaseCommand):
    """Run a saga to completion and require the compensator to have executed."""

    help = "Fail a task in a saga and require its compensator to run to SUCCEEDED."

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
        """Publish the saga, trigger it, and wait for the compensator to run."""
        opts = cast("dict[str, Any]", options)
        timeout = float(opts["timeout"])
        interval = float(opts["interval"])

        definition = WorkflowDefinition(
            name="saga-e2e",
            version=1,
            description="The compose saga end-to-end sample.",
            tasks=(
                TaskDefinition(key="reserve", task_type="echo", config={"value": "ok"}),
                TaskDefinition(
                    key="charge",
                    task_type="fail",
                    depends_on=("reserve",),
                    # One attempt only, so the run fails fast and rolls back
                    # instead of retrying for the full default backoff.
                    config={"retry": {"max_attempts": 1}, "message": "charge declined"},
                ),
                TaskDefinition(
                    key="undo_reserve",
                    task_type="noop",
                    compensate_for="reserve",
                ),
            ),
        )
        workflow, created = publish_definition(definition)
        self.stdout.write(f"Published saga-e2e (created={created}).")

        run, _ = trigger(workflow, requested_by_id=None)
        self.stdout.write(f"Triggered run {run.id}; waiting up to {timeout:.0f}s.")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run.refresh_from_db()
            compensator = TaskRun.objects.filter(workflow_run=run, task__key="undo_reserve").first()
            comp_state = compensator.state if compensator is not None else "absent"
            self.stdout.write(f"run={run.state} undo_reserve={comp_state}")

            if run.state == WorkflowRunState.FAILED.value:
                if compensator is None or compensator.state != TaskRunState.SUCCEEDED.value:
                    raise SystemExit(
                        f"Run {run.id} FAILED but the compensator did not succeed "
                        f"(state={comp_state}); the saga rollback did not run."
                    )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Saga run {run.id} FAILED and undo_reserve SUCCEEDED (rollback ran)."
                    )
                )
                return
            if run.state == WorkflowRunState.CANCELLED.value:
                raise SystemExit(f"Run {run.id} was cancelled.")
            time.sleep(interval)

        raise SystemExit(f"Saga run {run.id} did not reach FAILED within {timeout:.0f}s.")
