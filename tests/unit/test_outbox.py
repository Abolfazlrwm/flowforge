"""The OutboxMessage table and its invariants.

The outbox is the dual-write remedy: a dispatch event is a row here, written in
the same transaction that makes a task run dispatchable. These tests pin the
schema-level guarantees — defaults, the one-row-per-generation uniqueness, and the
delivery marker — that the scheduler and publisher rely on.
"""

from __future__ import annotations

from typing import Any

import pytest
from core.models import OutboxMessage
from django.db import IntegrityError

pytestmark = pytest.mark.django_db


def _make(task_run: Any, workflow_run: Any, **kwargs: Any) -> OutboxMessage:
    return OutboxMessage.objects.create(
        task_run=task_run,
        workflow_run=workflow_run,
        payload={"dispatch_id": "x", "task_run_id": str(task_run.id)},
        **kwargs,
    )


def test_outbox_defaults(task_run: object, workflow_run: object) -> None:
    """A fresh row carries the dispatch defaults and is undelivered."""
    message = _make(task_run, workflow_run)

    assert message.exchange == "flowforge.direct"
    assert message.routing_key == "task.dispatch"
    assert message.published_at is None
    assert message.fencing_token_snapshot == 0
    assert message.attempt_snapshot == 1
    assert message.created_at is not None
    # `id` is the stable dispatch identity, not a row we generate ourselves.
    assert message.id is not None


def test_one_outbox_row_per_generation(task_run: object, workflow_run: object) -> None:
    """Two rows for the same task run and fencing token must collide.

    The scheduler runs in multiple processes; this constraint is what makes
    exactly one of them win the race to dispatch a live generation.
    """
    _make(task_run, workflow_run, fencing_token_snapshot=0)

    with pytest.raises(IntegrityError):
        _make(task_run, workflow_run, fencing_token_snapshot=0)


def test_a_new_generation_is_a_new_row(task_run: object, workflow_run: object) -> None:
    """Re-dispatch after a reaped lease (a new fencing token) is allowed.

    The reaper bumps the token, so the re-dispatched generation is distinct and
    must not be blocked by the earlier row.
    """
    first = _make(task_run, workflow_run, fencing_token_snapshot=0)
    second = _make(task_run, workflow_run, fencing_token_snapshot=2)

    assert first.id != second.id
    assert OutboxMessage.objects.count() == 2
