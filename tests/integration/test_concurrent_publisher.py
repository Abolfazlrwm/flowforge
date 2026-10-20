"""Concurrency of the publisher's outbox drain against PostgreSQL.

Two publisher processes running at once must divide the pending queue without
overlap: ``SELECT ... FOR UPDATE SKIP LOCKED`` lets each take the rows the other
has not yet locked, so no dispatch_id is delivered twice. This is meaningless on
SQLite (it serialises writers and has no SKIP LOCKED), so it is gated to
PostgreSQL by :func:`require_postgres`.
"""

from __future__ import annotations

import pytest
from core.broker import FakePublisher, publish_once
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import OutboxMessage, Task, TaskRun, Workflow, WorkflowRun
from core.repositories import enqueue_ready_tasks

from tests.integration.conftest import require_postgres, run_concurrently

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    # transaction=True commits the seeded rows so the threads' separate
    # connections can see them. The default transaction-wrapped mode would hide
    # uncommitted rows from every thread and the publishers would find nothing.
    pytest.mark.django_db(transaction=True),
]


def _seed(count: int) -> None:
    """Create one run with ``count`` READY task runs, each with a pending outbox row."""
    workflow = Workflow.objects.create(name="concurrent", version=1)
    run = WorkflowRun.objects.create(workflow=workflow, state=WorkflowRunState.PENDING.value)
    for i in range(count):
        task = Task.objects.create(workflow=workflow, key=f"t{i}", task_type="noop")
        TaskRun.objects.create(workflow_run=run, task=task, state=TaskRunState.READY.value)
    enqueue_ready_tasks(run)


def test_two_publishers_divide_the_queue_without_overlap() -> None:
    """Concurrent publishers each deliver a disjoint share; together, all and only all."""
    require_postgres()
    _seed(20)
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 20

    def sweep(_index: int) -> list[str]:
        publisher = FakePublisher()
        publish_once(publisher)
        return [message["payload"]["dispatch_id"] for message in publisher.sent]

    batches = run_concurrently(2, sweep)
    delivered = [dispatch_id for batch in batches for dispatch_id in batch]

    # Exactly the 20 pending rows, each delivered once.
    assert len(delivered) == 20
    assert len(set(delivered)) == 20
    assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 0
