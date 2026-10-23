"""The append-only audit log: writing, redaction, and database-level immutability.

The immutability tests go through ``QuerySet.update()`` / ``.delete()``, which
bypass every Python-level guard, so what they prove is the database trigger, not
a ``save()`` override.
"""

from __future__ import annotations

import pytest
from core.models import AuditLog, Organization
from core.security import REDACTED
from core.services import audit
from django.contrib.auth.models import User
from django.db import DatabaseError, transaction


@pytest.mark.django_db
class TestRecord:
    """``record`` appends one entry and snapshots the actor."""

    def test_a_record_is_created(self, organization: Organization) -> None:
        """The minimal entry."""
        entry = audit.record(
            organization=organization,
            action="workflow.published",
            target_type="workflow",
            target_id="abc",
        )

        assert AuditLog.objects.filter(pk=entry.pk).exists()
        assert entry.actor_label == ""
        assert entry.target_id == "abc"

    def test_the_actor_label_is_snapshotted(self, organization: Organization) -> None:
        """Deleting the user must not erase who acted."""
        actor = User.objects.create_user("alice", password="pw12345!")

        entry = audit.record(
            organization=organization,
            action="run.triggered",
            target_type="run",
            target_id="r1",
            actor=actor,
        )

        assert entry.actor_label == "alice"
        assert entry.actor_id == actor.pk

    def test_metadata_is_redacted_before_it_is_written(self, organization: Organization) -> None:
        """A secret must never reach the audit trail."""
        entry = audit.record(
            organization=organization,
            action="api_key.created",
            target_type="api_key",
            target_id="k1",
            metadata={
                "name": "ci",
                "password": "hunter2",
                "nested": {"api_token": "abc"},
                "safe": "visible",
            },
        )

        assert entry.metadata["name"] == "ci"
        assert entry.metadata["password"] == REDACTED
        assert entry.metadata["nested"]["api_token"] == REDACTED
        assert entry.metadata["safe"] == "visible"


@pytest.mark.django_db
class TestAppendOnly:
    """The database refuses UPDATE and DELETE, whoever issues them."""

    def _entry(self, organization: Organization) -> AuditLog:
        return audit.record(
            organization=organization,
            action="workflow.published",
            target_type="workflow",
            target_id="w1",
        )

    def test_update_is_rejected(self, organization: Organization) -> None:
        """A rewritten audit row is a lie; the database refuses it."""
        entry = self._entry(organization)

        with pytest.raises(DatabaseError), transaction.atomic():
            AuditLog.objects.filter(pk=entry.pk).update(action="tampered")

    def test_delete_is_rejected(self, organization: Organization) -> None:
        """History cannot be erased one row at a time."""
        entry = self._entry(organization)

        with pytest.raises(DatabaseError), transaction.atomic():
            AuditLog.objects.filter(pk=entry.pk).delete()
