"""Mutating API actions leave an audit trail.

The log itself is tested for immutability in ``tests/unit/test_audit_log.py``;
here the question is whether the endpoints actually write to it.
"""

from __future__ import annotations

from typing import Any

import pytest
from core.models import AuditLog
from django.urls import reverse
from rest_framework.test import APIClient

from tests.unit.api.payloads import definition_payload


@pytest.mark.django_db
class TestAuditTrail:
    """Publishing, triggering and key management are audited."""

    def test_publishing_is_audited(self, staff_client: APIClient, organization: Any) -> None:
        """A new definition is a security-relevant change."""
        staff_client.post(reverse("workflow-list"), definition_payload(), format="json")

        entry = AuditLog.objects.get(action="workflow.published")
        assert entry.organization_id == organization.pk
        assert entry.actor_label == "root"
        assert entry.target_type == "workflow"

    def test_triggering_is_audited(self, user_client: APIClient, published: Any) -> None:
        """Starting work is recorded against the caller."""
        user_client.post(reverse("run-list"), {"name": "etl", "version": 1}, format="json")

        entry = AuditLog.objects.get(action="run.triggered")
        assert entry.actor_label == "alice"
        assert entry.target_type == "run"

    def test_creating_an_api_key_is_audited(self, staff_client: APIClient) -> None:
        """Issuing a credential must be attributable."""
        staff_client.post(
            reverse("api-key-list"), {"name": "ci", "scopes": ["read"]}, format="json"
        )

        entry = AuditLog.objects.get(action="api_key.created")
        assert entry.metadata["name"] == "ci"
        assert entry.actor_label == "root"

    def test_republishing_is_not_a_new_audit_entry(self, staff_client: APIClient) -> None:
        """A no-op republish did not change anything, so it is not recorded."""
        url = reverse("workflow-list")
        staff_client.post(url, definition_payload(), format="json")
        staff_client.post(url, definition_payload(), format="json")

        assert AuditLog.objects.filter(action="workflow.published").count() == 1
