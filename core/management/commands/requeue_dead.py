"""Requeue a DEAD task run — the manual half of the dead-letter queue.

A ``DEAD`` task run is terminal: its retry policy was exhausted (or its lease
expired on the last allowed attempt), so nothing will ever move it again. That is
deliberate — an automatic retry of a task that has already failed
``max_attempts`` times is a loop, not a recovery.

Recovery is therefore an operator decision. This command is that decision: it
resets the task run to ``PENDING`` with a fresh attempt budget and reverts its
workflow run to ``PENDING``, so the next scheduler pass re-evaluates the run and
re-dispatches the task. Its result is cleared and its fencing token is bumped, so
any in-flight reference to the dead generation is invalidated.

Usage::

    manage.py requeue_dead <task_run_id>
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any, cast
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.repositories.transitions import requeue_dead_task_run


class Command(BaseCommand):
    """Reset one DEAD task run so the scheduler will dispatch it again."""

    help = "Requeue a DEAD task run for another attempt (manual dead-letter recovery)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the task run id."""
        parser.add_argument(
            "task_run_id",
            help="UUID of the DEAD task run to requeue.",
        )

    def handle(self, *_args: object, **options: object) -> None:
        """Requeue the task run, or fail loudly if it is not DEAD.

        A non-zero exit is important: an operator scripting a recovery loop must
        not read "nothing happened" as success.
        """
        opts = cast("dict[str, Any]", options)
        raw_id = str(opts["task_run_id"])
        try:
            task_run_id = UUID(raw_id)
        except ValueError:
            raise CommandError(f"{raw_id!r} is not a valid UUID.") from None

        if not requeue_dead_task_run(task_run_id=task_run_id):
            raise CommandError(
                f"Task run {task_run_id} was not requeued: it does not exist or is not DEAD. "
                "Only DEAD task runs can be requeued."
            )

        self.stdout.write(f"Requeued task run {task_run_id}; the scheduler will re-dispatch it.")
