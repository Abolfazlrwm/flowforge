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
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from core.domain.definition import WorkflowDefinition
from core.domain.services import initial_task_run_state
from core.models import IdempotencyKey, Task, TaskRun, Workflow, WorkflowRun
from core.repositories import idempotency
from core.repositories.definitions import load_definition


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
    idempotency_key: str | None = None,
    fingerprint: str = "",
) -> tuple[WorkflowRun, bool]:
    """Create a run of ``workflow`` and its task runs.

    Args:
        workflow: The published definition to instantiate.
        requested_by_id: The user who asked. ``None`` for a system trigger.
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
    if idempotency_key is None:
        with transaction.atomic():
            return _materialise(workflow, requested_by_id=requested_by_id), True

    try:
        with transaction.atomic():
            # The reservation must share the transaction with the run: if the run
            # cannot be created, the claim rolls back with it and the key is free
            # again rather than replaying as an empty response forever.
            claim = idempotency.reserve(
                user_id=requested_by_id or 0,
                key=idempotency_key,
                fingerprint=fingerprint,
            )
            run = _materialise(workflow, requested_by_id=requested_by_id)
            idempotency.complete(claim, status=202, body={}, workflow_run_id=run.pk)
            return run, True
    except IntegrityError:
        existing = idempotency.find(user_id=requested_by_id or 0, key=idempotency_key)
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


def _materialise(workflow: Workflow, *, requested_by_id: int | None) -> WorkflowRun:
    """Create the run row and one task run per task.

    Reads the definition through the same lossless path the publish side uses, so
    the run is instantiated from validated content rather than from whatever the
    rows happen to say.
    """
    definition: WorkflowDefinition = load_definition(workflow)

    run = WorkflowRun.objects.create(workflow=workflow, requested_by_id=requested_by_id)
    tasks_by_key = {task.key: task for task in Task.objects.filter(workflow=workflow)}
    TaskRun.objects.bulk_create(
        [
            TaskRun(
                workflow_run=run,
                task=tasks_by_key[task.key],
                state=initial_task_run_state(dependency_count=len(task.depends_on)).value,
                # The handler idempotency key, if the task declares one. Two task
                # runs with the same key execute the handler once; the second reuses
                # the first's persisted result. Null means "not idempotent".
                idempotency_key=task.config.get("idempotency_key"),
            )
            for task in definition.tasks
        ]
    )
    return run


def runs_visible_to(user: Any) -> QuerySet[WorkflowRun]:
    """The runs a user may list.

    Staff see everything; everyone else sees their own. Filtering here rather
    than in the view means the rule cannot be forgotten by a second view.
    """
    queryset = WorkflowRun.objects.select_related("workflow").order_by("-created_at", "-id")
    if user.is_staff:
        return queryset
    return queryset.filter(requested_by_id=user.pk)


__all__ = [
    "IdempotencyKey",
    "request_fingerprint",
    "runs_visible_to",
    "trigger",
]
