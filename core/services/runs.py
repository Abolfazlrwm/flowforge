"""Triggering a workflow run.

This is the one place a definition turns into an execution. A view must not do
this: creating a run means materialising one ``TaskRun`` per task with the
correct initial state, and doing it idempotently under a race. That is
orchestration, and it belongs here.

What this module deliberately does **not** do is schedule anything. Every task
run is created in its initial state — ``READY`` if it has no dependencies,
``PENDING`` otherwise — and nothing claims them. Picking the next task to run is
Phase 4.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from core.domain.definition import WorkflowDefinition
from core.domain.services import initial_task_run_state
from core.domain.states import WorkflowRunState
from core.models import IdempotencyKey, Organization, Task, TaskRun, Workflow, WorkflowRun
from core.repositories import idempotency
from core.repositories.definitions import load_definition
from core.repositories.transitions import (
    CANCELLABLE_TASK_STATES,
    cancel_task_run,
    transition_workflow_run,
)


def request_fingerprint(payload: Any) -> str:
    """A stable digest of a request body, for detecting key reuse.

    Sorted keys and no whitespace, so two requests that differ only in key order
    are the same request. This is a change-detector, not a security control: it
    does not need a salt, and a collision would have to be produced by a client
    against itself.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def trigger(
    workflow: Workflow,
    *,
    requested_by_id: int | None,
    organization: Organization | None = None,
    idempotency_key: str | None = None,
    fingerprint: str = "",
) -> tuple[WorkflowRun, bool]:
    """Create a run of ``workflow`` and its task runs.

    Args:
        workflow: The published definition to instantiate.
        requested_by_id: The user who asked. ``None`` for a system trigger.
        organization: The tenant that owns the run. Defaults to the workflow's
            own organization, which is what the scheduler uses; an API caller
            passes the active organization explicitly.
        idempotency_key: The client's ``Idempotency-Key`` header, if any.
        fingerprint: Digest of the request body. Only used when a key is given.

    Returns:
        The run, and whether it was created by this call. ``False`` means a
        previous request with the same key created it and this call replayed
        that result.

    Raises:
        IdempotencyMismatchError: if the key was used with a different body.
        IntegrityError: if a concurrent request won the key and the caller
            retries with a stale fingerprint — the caller of this function does
            not see this; it is handled below.
    """
    org = organization if organization is not None else workflow.organization

    if idempotency_key is None:
        with transaction.atomic():
            return _materialise(workflow, requested_by_id=requested_by_id, organization=org), True

    try:
        with transaction.atomic():
            # The reservation must share the transaction with the run: if the run
            # cannot be created, the claim rolls back with it and the key is free
            # again rather than replaying as an empty response forever.
            claim = idempotency.reserve(
                organization=org,
                user_id=requested_by_id or 0,
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            run = _materialise(workflow, requested_by_id=requested_by_id, organization=org)
            idempotency.complete(claim, status=202, body={}, workflow_run_id=run.pk)
            return run, True
    except IntegrityError:
        existing = idempotency.find(
            organization=org, user_id=requested_by_id or 0, key=idempotency_key
        )
        if existing is None:
            # Not a lost race on the key — some other constraint failed. Do not
            # disguise it as a replay.
            raise
        if existing.request_fingerprint != fingerprint:
            raise idempotency.IdempotencyMismatchError(idempotency_key) from None
        if existing.workflow_run is None:
            # Unreachable while `complete` runs in the same transaction: a claim
            # is only visible once its run is committed. Kept as an explicit
            # failure rather than a silent None return.
            raise RuntimeError(
                f"idempotency claim {idempotency_key!r} committed without a workflow run"
            ) from None
        return existing.workflow_run, False


def _materialise(
    workflow: Workflow, *, requested_by_id: int | None, organization: Organization | None
) -> WorkflowRun:
    """Create the run row and one task run per task.

    Reads the definition through the same lossless path the publish side uses, so
    the run is instantiated from validated content rather than from whatever the
    rows happen to say. Every row is stamped with ``organization`` so the whole
    run is tenant-scoped from creation, not by a later filter.
    """
    definition: WorkflowDefinition = load_definition(workflow)

    run = WorkflowRun.objects.create(
        workflow=workflow, organization=organization, requested_by_id=requested_by_id
    )
    tasks_by_key = {task.key: task for task in Task.objects.filter(workflow=workflow)}
    TaskRun.objects.bulk_create(
        [
            TaskRun(
                workflow_run=run,
                organization=organization,
                task=tasks_by_key[task.key],
                state=initial_task_run_state(dependency_count=len(task.depends_on)).value,
                # The handler idempotency key, if the task declares one. Two task
                # runs with the same key execute the handler once; the second reuses
                # the first's persisted result. Null means "not idempotent".
                idempotency_key=task.config.get("idempotency_key"),
            )
            for task in definition.tasks
            # A compensation task is not part of the forward DAG: it gets no task
            # run until the run fails and the scheduler triggers the rollback.
            if task.compensate_for is None
        ]
    )
    return run


def runs_visible_to(organization: Organization) -> QuerySet[WorkflowRun]:
    """The runs of one organization, newest first.

    Tenant scoping lives here rather than in each view so a second view cannot
    forget it: every run list, detail and event-log read goes through this
    function, and ``for_organization`` makes another tenant's runs invisible by
    construction (a lookup by their id returns 404, not 403).
    """
    return (
        WorkflowRun.objects.for_organization(organization)
        .select_related("workflow")
        .order_by("-created_at", "-id")
    )


def request_cancellation(run_id: UUID, *, now: datetime | None = None) -> int:
    """Cancel a run and every task of it that has not resolved yet.

    The run is locked first, so two concurrent cancellations cannot both count
    the same tasks. Each task is cancelled with its own conditional ``UPDATE``
    (see :func:`cancel_task_run`), which is what makes this safe against a worker
    finishing at the same instant: whichever statement runs first wins, and the
    loser matches zero rows.

    A run that is already terminal is left alone — cancelling a ``SUCCEEDED`` run
    would rewrite history, and there is nothing left to stop.

    Returns:
        The number of task runs cancelled. Zero means the run was already
        terminal (or every task had already resolved).

    Raises:
        WorkflowRun.DoesNotExist: if no run has that id.
    """
    moment = now if now is not None else timezone.now()
    with transaction.atomic():
        run = WorkflowRun.objects.select_for_update().get(pk=run_id)
        if run.state not in (WorkflowRunState.PENDING.value, WorkflowRunState.RUNNING.value):
            return 0

        cancelled = sum(
            1
            for task_run in TaskRun.objects.filter(
                workflow_run=run, state__in=CANCELLABLE_TASK_STATES
            )
            if cancel_task_run(task_run_id=task_run.id, now=moment)
        )
        transition_workflow_run(
            run_id=run.id,
            expected=WorkflowRunState(run.state),
            target=WorkflowRunState.CANCELLED,
            now=moment,
        )
        return cancelled


__all__ = [
    "IdempotencyKey",
    "request_cancellation",
    "request_fingerprint",
    "runs_visible_to",
    "trigger",
]
