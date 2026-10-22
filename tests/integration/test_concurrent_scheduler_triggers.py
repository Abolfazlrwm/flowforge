"""No-double-fire for scheduled triggers across scheduler instances.

With N scheduler processes polling the same table, every due trigger must fire
exactly once. ``SELECT FOR UPDATE SKIP LOCKED`` is the mechanism: the first
instance to grab a due row advances it and triggers; the rest skip locked rows.
This needs real PostgreSQL — SQLite serialises writers and would make the race
unobservable, which is why this lives in the integration suite and skips locally.

Run with ``REQUIRE_POSTGRES=1`` in CI; locally it is skipped.
"""

from __future__ import annotations

import pytest
from core.models import ScheduledTrigger, Task, Workflow, WorkflowRun
from core.repositories.scheduler import tick_scheduled_triggers

from tests.conftest import NOW
from tests.integration.conftest import THREADS, require_postgres, run_concurrently

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]

#: Five distinct workflows, each with one due trigger.
TRIGGERS = 5


def _published_workflow(name: str) -> Workflow:
    """A workflow with one task — i.e. one that has been published."""
    workflow = Workflow.objects.create(name=name, version=1)
    Task.objects.create(workflow=workflow, key="extract", task_type="extract")
    return workflow


def test_due_triggers_fire_exactly_once_across_instances() -> None:
    """N schedulers racing the same due set produce exactly one run per trigger.

    The winner locks every due row, advances each clock, and triggers; the other
    instances skip the now-locked (or now-future) rows and fire nothing. Under any
    interleaving the total number of runs equals the number of triggers.
    """
    require_postgres()
    for index in range(TRIGGERS):
        workflow = _published_workflow(f"wf-{index}")
        ScheduledTrigger.objects.create(
            workflow=workflow,
            cron_expression="* * * * *",
            enabled=True,
            next_run_at=NOW,
        )
    assert ScheduledTrigger.objects.count() == TRIGGERS

    # Every scheduler instance ticks with the same clock, concurrently.
    run_concurrently(THREADS, lambda _index: tick_scheduled_triggers(now=NOW))

    # Exactly one run per trigger, regardless of how the threads interleaved.
    assert WorkflowRun.objects.count() == TRIGGERS
    for trigger in ScheduledTrigger.objects.all():
        # Each advanced past NOW, so a second pass would not re-fire it.
        assert trigger.next_run_at > NOW
