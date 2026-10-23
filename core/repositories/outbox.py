"""The transactional outbox: the scheduler writes here, the publisher reads here.

This module is the broker-agnostic half of the outbox. It never imports pika (or any
AMQP client), so it can be unit-tested with a fake publisher and exercised against
real PostgreSQL in CI. The publisher side of the read path (`fetch_pending`,
`mark_published`) lives here too so the row shape stays in one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import OutboxMessage, TaskRun, WorkflowRun


def build_dispatch_payload(
    *,
    dispatch_id: UUID,
    task_run: TaskRun,
    fencing_token_snapshot: int,
    attempt_snapshot: int,
) -> dict[str, Any]:
    """The message body a worker receives for this dispatch.

    It carries the dispatch id (for redelivery de-duplication), the task identity and
    a snapshot of its configuration, so the worker needs no further lookup to
    execute. ``fencing_token_snapshot`` is what the worker must present to
    ``finish_task_run`` — the same value the scheduler observed when it enqueued.

    ``task`` and ``workflow_run_id`` are derived from ``task_run`` so the caller
    cannot pass a mismatched payload — the message always describes the row it is
    written for.
    """
    task = task_run.task
    return {
        "dispatch_id": str(dispatch_id),
        # Carried for observability: the worker logs the tenant it is acting for,
        # so a log line can be attributed without a second lookup. It does not
        # change execution — the task run already belongs to exactly one tenant.
        "organization_id": str(task_run.organization_id) if task_run.organization_id else None,
        "task_run_id": str(task_run.id),
        "workflow_run_id": str(task_run.workflow_run_id),
        "task_key": task.key,
        "task_type": task.task_type,
        "config": task.config,
        "attempt": attempt_snapshot,
        "fencing_token": fencing_token_snapshot,
        # Carried so the worker can short-circuit a repeat execution: if another
        # SUCCEEDED task run already holds this key with a result, the handler is
        # not re-run and that result is reused (see `core.worker.consumer`).
        "idempotency_key": task_run.idempotency_key,
    }


def create_dispatch(
    *,
    task_run: TaskRun,
    workflow_run: WorkflowRun,
    fencing_token_snapshot: int,
    attempt_snapshot: int,
) -> OutboxMessage | None:
    """Record one dispatch generation for ``task_run``, idempotently.

    Returns the new row, or ``None`` if a row for ``(task_run,
    fencing_token_snapshot)`` already exists — another scheduler won the race, or this
    scheduler already enqueued it. The unique constraint is the guarantee; the
    ``IntegrityError`` branch is the PostgreSQL-safe way to observe it.

    Must be called inside the transaction that made the task run dispatchable, so the
    outbox row and the eligibility work commit together (the dual-write remedy).
    """
    # The common "this generation is already dispatched" path must not attempt an
    # INSERT: on SQLite a failed statement inside the caller's transaction.atomic()
    # aborts the whole transaction, and the next query then raises
    # TransactionManagementError. The pre-check returns early so the steady state is
    # exception-free. The unique constraint still resolves the rare cross-process race;
    # the savepoint below contains that failure so the caller's transaction survives.
    if OutboxMessage.objects.filter(
        task_run=task_run, fencing_token_snapshot=fencing_token_snapshot
    ).exists():
        return None
    dispatch_id = uuid4()
    payload = build_dispatch_payload(
        dispatch_id=dispatch_id,
        task_run=task_run,
        fencing_token_snapshot=fencing_token_snapshot,
        attempt_snapshot=attempt_snapshot,
    )
    try:
        with transaction.atomic():
            return OutboxMessage.objects.create(
                id=dispatch_id,
                # The dispatch belongs to the same tenant as the task run it
                # dispatches; use the FK id so this stays a single INSERT.
                organization_id=task_run.organization_id,
                task_run=task_run,
                workflow_run=workflow_run,
                fencing_token_snapshot=fencing_token_snapshot,
                attempt_snapshot=attempt_snapshot,
                payload=payload,
            )
    except IntegrityError:
        return None


def fetch_pending(limit: int = 1000) -> list[OutboxMessage]:
    """Undelivered rows, locked for this publisher so a peer cannot take them.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` over the partial index on
    ``created_at WHERE published_at IS NULL``, so two publishers divide the queue
    without blocking each other and a crashed publisher's rows become available to
    the next sweep. Returns at most ``limit`` rows in creation order.
    """
    return list(
        OutboxMessage.objects.select_for_update(skip_locked=True)
        .filter(published_at__isnull=True)
        .order_by("created_at")[:limit]
    )


def mark_published(message_id: UUID, *, now: datetime | None = None) -> bool:
    """Mark a row delivered.

    Returns ``True`` if this call set the timestamp, ``False`` if it was already
    marked (another publisher claimed the row between fetch and publish). The
    publisher treats ``False`` as "someone else finished it" and moves on.
    """
    moment = now if now is not None else timezone.now()
    return (
        OutboxMessage.objects.filter(pk=message_id, published_at__isnull=True).update(
            published_at=moment
        )
        == 1
    )
