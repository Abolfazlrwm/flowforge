"""Writing append-only audit entries.

Every security-relevant action funnels through :func:`record`, which is the one
place that (a) snapshots the actor's label and (b) redacts the metadata. Keeping
both here means a new call site cannot forget either.

The table itself refuses UPDATE and DELETE at the database level (migration
``0015``); this module only ever inserts.
"""

from __future__ import annotations

from typing import Any

from core.models import AuditLog, Organization
from core.security import redact_mapping


def record(  # noqa: PLR0913 - one explicit keyword per audit field is the point
    *,
    organization: Organization | None,
    action: str,
    target_type: str,
    target_id: object,
    actor: Any = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit entry.

    Args:
        organization: The tenant the action belongs to. ``None`` is accepted for
            legacy rows whose tenant predates the backfill; those entries are
            visible only to staff.
        action: A dotted action name, e.g. ``workflow.published``.
        target_type: The kind of thing acted on, e.g. ``workflow``.
        target_id: The target's identifier, stored as text.
        actor: The user who performed the action, if any.
        metadata: Extra context. Redacted before it is written, so a secret can
            never enter the audit trail through this argument.
    """
    actor_label = ""
    if actor is not None:
        actor_label = str(getattr(actor, "username", "") or actor)[:200]
    return AuditLog.objects.create(
        organization=organization,
        actor=actor if getattr(actor, "pk", None) is not None else None,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata=redact_mapping(metadata or {}),
    )
