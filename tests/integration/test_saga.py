"""Saga compensation on a real PostgreSQL server.

The unit suite proves the ordering logic; this proves the dispatch path on
PostgreSQL — that a triggered compensator is written to the outbox like any other
task and that the run is held ``RUNNING`` until the rollback settles. It skips
locally and runs in CI with ``REQUIRE_POSTGRES=1``.
"""

from __future__ import annotations

import pytest
from core.domain.schema import parse_workflow_definition
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import OutboxMessage, TaskRun, WorkflowRun
from core.repositories.definitions import publish_definition
from core.repositories.scheduler import advance_workflow_run
from core.services.runs import trigger

from tests.integration.conftest import require_postgres

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db(transaction=True),
]


def test_a_triggered_compensator_is_dispatched_and_the_run_waits() -> None:
    """Reserve succeeded and charge failed, so undo_reserve is dispatched.

    The compensator gets an outbox row exactly like a forward task, and the run
    stays RUNNING until the compensator resolves — only then does it settle FAILED.
    """
    require_postgres()
    workflow, _created = publish_definition(
        parse_workflow_definition(
            {
                "name": "saga",
                "version": 1,
                "tasks": [
                    {"key": "reserve", "task_type": "noop"},
                    {"key": "charge", "task_type": "noop", "depends_on": ["reserve"]},
                    {"key": "undo_reserve", "task_type": "noop", "compensate_for": "reserve"},
                ],
            }
        )
    )
    run, _ = trigger(workflow, requested_by_id=None)
    WorkflowRun.objects.filter(pk=run.id).update(state=WorkflowRunState.RUNNING.value)
    TaskRun.objects.filter(workflow_run=run, task__key="reserve").update(
        state=TaskRunState.SUCCEEDED.value
    )
    TaskRun.objects.filter(workflow_run=run, task__key="charge").update(
        state=TaskRunState.FAILED.value
    )

    advance_workflow_run(run)

    assert OutboxMessage.objects.filter(task_run__task__key="undo_reserve").count() == 1
    run.refresh_from_db()
    assert run.state == WorkflowRunState.RUNNING.value  # still rolling back

    # The compensator succeeds; the run now settles FAILED.
    TaskRun.objects.filter(workflow_run=run, task__key="undo_reserve").update(
        state=TaskRunState.SUCCEEDED.value
    )
    advance_workflow_run(run)
    run.refresh_from_db()
    assert run.state == WorkflowRunState.FAILED.value
