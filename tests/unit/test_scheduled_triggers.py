"""The scheduled-trigger tick: due triggers fire their workflow exactly once.

These run on SQLite. The eligibility and clock-advance logic are pure row
operations, so they do not need PostgreSQL. The no-double-fire-across-instances
race is covered separately in the integration suite, on real PostgreSQL where
``SKIP LOCKED`` actually means something.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from core.models import ScheduledTrigger, Task, Workflow, WorkflowRun
from core.repositories.scheduler import schedule_once, tick_scheduled_triggers
from core.services import runs as run_service

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


def _published_workflow(name: str = "etl") -> Workflow:
    """A workflow with one task — i.e. one that has been published."""
    workflow = Workflow.objects.create(name=name, version=1)
    Task.objects.create(workflow=workflow, key="extract", task_type="extract")
    return workflow


def _due_trigger(
    *,
    workflow: Workflow,
    cron: str = "* * * * *",
    enabled: bool = True,
    next_run_at=NOW,
) -> ScheduledTrigger:
    """A trigger whose ``next_run_at`` is already due at ``NOW`` by default."""
    return ScheduledTrigger.objects.create(
        workflow=workflow,
        cron_expression=cron,
        enabled=enabled,
        next_run_at=next_run_at,
    )


def test_a_due_trigger_fires_its_workflow_and_advances_the_clock() -> None:
    """A single due trigger creates exactly one run and moves to the next tick."""
    trigger = _due_trigger(workflow=_published_workflow())

    fired = tick_scheduled_triggers(now=NOW)

    assert fired == 1
    assert WorkflowRun.objects.count() == 1
    trigger.refresh_from_db()
    # ``* * * * *`` from 12:00 advances to 12:01.
    assert trigger.next_run_at == NOW + timedelta(minutes=1)
    assert trigger.last_run_at == NOW
    # Having fired, it is no longer due at NOW.
    assert tick_scheduled_triggers(now=NOW) == 0


def test_the_clock_advances_even_when_the_trigger_raises() -> None:
    """A crashing trigger is a missing run, never a double-fire: the clock moved.

    This is the property that makes recovery safe — after a crash mid-flight the
    trigger already says "I fired at this time", so a retry on the same row would
    be a duplicate, not a make-good.
    """
    trigger = _due_trigger(workflow=_published_workflow())

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("trigger failed")

    # `trigger` is imported locally inside `tick_scheduled_triggers`, so patch it
    # at the source module.
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(run_service, "trigger", _boom)
        fired = tick_scheduled_triggers(now=NOW)

    assert fired == 0
    assert WorkflowRun.objects.count() == 0
    trigger.refresh_from_db()
    assert trigger.next_run_at == NOW + timedelta(minutes=1)
    assert trigger.last_run_at == NOW


def test_a_disabled_trigger_is_never_fired() -> None:
    """Disabled triggers are not selected, so they neither fire nor advance."""
    trigger = _due_trigger(workflow=_published_workflow(), enabled=False)

    assert tick_scheduled_triggers(now=NOW) == 0
    assert WorkflowRun.objects.count() == 0
    trigger.refresh_from_db()
    assert trigger.next_run_at == NOW  # untouched


def test_an_unpublished_workflow_is_skipped_but_its_clock_advances() -> None:
    """A trigger whose workflow has no tasks is a dangling reference.

    The clock still advances (the operator must fix the workflow, not the clock),
    but no run is created.
    """
    draft = Workflow.objects.create(name="draft", version=1)  # no tasks
    trigger = _due_trigger(workflow=draft)

    assert tick_scheduled_triggers(now=NOW) == 0
    assert WorkflowRun.objects.count() == 0
    trigger.refresh_from_db()
    assert trigger.next_run_at == NOW + timedelta(minutes=1)
    assert trigger.last_run_at == NOW


def test_a_trigger_due_in_the_future_is_not_fired() -> None:
    """Only triggers whose ``next_run_at`` is at or before ``now`` are eligible."""
    _due_trigger(workflow=_published_workflow(), next_run_at=NOW + timedelta(hours=1))

    assert tick_scheduled_triggers(now=NOW) == 0
    assert WorkflowRun.objects.count() == 0


def test_schedule_once_fires_due_triggers_in_the_same_pass() -> None:
    """Wiring the tick into ``schedule_once`` covers both the loop and ``--once``.

    A due trigger's fresh run is eligible for scheduling in the very same pass,
    not the next one.
    """
    _due_trigger(workflow=_published_workflow())

    reaped, enqueued = schedule_once()

    assert reaped == 0
    assert WorkflowRun.objects.count() == 1
    # The triggered run has one ready task, so it gets one dispatch.
    assert enqueued == 1
