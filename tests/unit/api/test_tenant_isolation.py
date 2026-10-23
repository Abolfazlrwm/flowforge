"""Tenant isolation and the role-permission matrix.

Two properties are asserted, and they are different:

1. **Isolation.** A caller from another organization cannot reach a row by id.
   The endpoint must answer **404**, not 403 — the row must be invisible, so its
   existence does not leak. This is checked for every tenant-addressed endpoint.
2. **Authorization.** Inside one organization, the role decides what a caller may
   do. ``viewer < member < admin < owner``; the matrix below pins every cell that
   matters.

API-key lifecycle (expired, revoked, wrong scope, rotated) is covered in
``test_api_keys.py``; the cross-tenant key actions are covered here.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from core.domain.states import TaskRunState
from core.models import Membership, Organization, TaskRun
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from tests.unit.api.payloads import definition_payload


def _client_for(organization: Organization, role: str, label: str) -> APIClient:
    """A non-staff client with ``role`` in ``organization``."""
    username = f"{label}-{uuid.uuid4().hex[:8]}"
    user = User.objects.create_user(username, password="pw12345!")
    Membership.objects.create(organization=organization, user=user, role=role)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    return client


def _trigger(client: APIClient) -> Any:
    """Trigger ``etl`` v1."""
    return client.post(reverse("run-list"), {"name": "etl", "version": 1}, format="json")


def _extract_task_run_id(payload: Any) -> Any:
    """The id of the dependency-free ``extract`` task run."""
    return next(tr["id"] for tr in payload["task_runs"] if tr["task_key"] == "extract")


def _fail(task_run_id: Any) -> None:
    """Drive a task run into FAILED."""
    TaskRun.objects.filter(pk=task_run_id).update(
        state=TaskRunState.FAILED.value, attempt=3, finished_at=timezone.now()
    )


@pytest.mark.django_db
class TestCrossTenantIs404:
    """Another tenant's identifiers are invisible, never merely forbidden."""

    def test_another_tenants_definition_is_404(
        self, stranger_client: APIClient, published: Any
    ) -> None:
        """The definition exists, but not for this caller."""
        response = stranger_client.get(
            reverse("workflow-detail", kwargs={"name": "etl", "version": 1})
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_triggering_another_tenants_definition_is_404(
        self, stranger_client: APIClient, published: Any
    ) -> None:
        """A name/version pair is scoped to the caller's organization."""
        assert _trigger(stranger_client).status_code == status.HTTP_404_NOT_FOUND

    def test_another_tenants_run_is_404(
        self, user_client: APIClient, stranger_client: APIClient, published: Any
    ) -> None:
        """The run detail does not leak existence across tenants."""
        created = _trigger(user_client)
        response = stranger_client.get(reverse("run-detail", kwargs={"pk": created.data["id"]}))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_another_tenants_event_log_is_404(
        self, user_client: APIClient, stranger_client: APIClient, published: Any
    ) -> None:
        """The event log is scoped the same way as the run."""
        created = _trigger(user_client)
        response = stranger_client.get(reverse("run-events", kwargs={"pk": created.data["id"]}))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_another_tenants_task_run_cannot_be_requeued(
        self, user_client: APIClient, stranger_client: APIClient, published: Any
    ) -> None:
        """A cross-tenant requeue is a 404, not a write."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)
        _fail(task_run_id)

        response = stranger_client.post(reverse("task-run-requeue", kwargs={"pk": task_run_id}))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_another_tenants_api_key_cannot_be_rotated(
        self,
        staff_client: APIClient,
        other_organization: Organization,
        db: None,
    ) -> None:
        """An admin of another tenant cannot rotate a key they cannot see."""
        created = staff_client.post(
            reverse("api-key-list"), {"name": "ci", "scopes": ["read"]}, format="json"
        )
        other_admin = _client_for(other_organization, Membership.Role.ADMIN, "other-admin")

        response = other_admin.post(reverse("api-key-rotate", kwargs={"pk": created.data["id"]}))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_another_tenants_api_key_cannot_be_revoked(
        self,
        staff_client: APIClient,
        other_organization: Organization,
        db: None,
    ) -> None:
        """Revocation is equally scoped."""
        created = staff_client.post(
            reverse("api-key-list"), {"name": "ci", "scopes": ["read"]}, format="json"
        )
        other_admin = _client_for(other_organization, Membership.Role.ADMIN, "other-admin")

        response = other_admin.post(reverse("api-key-revoke", kwargs={"pk": created.data["id"]}))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestRoleMatrix:
    """Inside one organization, the role decides what is allowed."""

    @pytest.mark.parametrize(
        ("role", "trigger_status", "publish_status", "keys_status"),
        [
            (
                Membership.Role.VIEWER,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_403_FORBIDDEN,
            ),
            (
                Membership.Role.MEMBER,
                status.HTTP_202_ACCEPTED,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_403_FORBIDDEN,
            ),
            (
                Membership.Role.ADMIN,
                status.HTTP_202_ACCEPTED,
                status.HTTP_201_CREATED,
                status.HTTP_200_OK,
            ),
            (
                Membership.Role.OWNER,
                status.HTTP_202_ACCEPTED,
                status.HTTP_201_CREATED,
                status.HTTP_200_OK,
            ),
        ],
    )
    def test_the_role_decides(  # noqa: PLR0913, PLR0917 - pytest fixtures + params
        self,
        organization: Organization,
        published: Any,
        role: str,
        trigger_status: int,
        publish_status: int,
        keys_status: int,
        db: None,
    ) -> None:
        """One row per role, every cell asserted."""
        client = _client_for(organization, role, role)

        # Every role may read.
        assert client.get(reverse("run-list")).status_code == status.HTTP_200_OK
        assert client.get(reverse("workflow-list")).status_code == status.HTTP_200_OK

        # Triggering needs member+.
        assert _trigger(client).status_code == trigger_status

        # Publishing needs admin+.
        published_response = client.post(
            reverse("workflow-list"), definition_payload(name=f"wf-{role}"), format="json"
        )
        assert published_response.status_code == publish_status

        # Key management needs admin+.
        assert client.get(reverse("api-key-list")).status_code == keys_status

    def test_a_viewer_cannot_requeue(
        self, organization: Organization, user_client: APIClient, published: Any
    ) -> None:
        """A viewer may read but not recover work."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)
        _fail(task_run_id)
        viewer = _client_for(organization, Membership.Role.VIEWER, "viewer")

        response = viewer.post(reverse("task-run-requeue", kwargs={"pk": task_run_id}))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_member_can_requeue(
        self, organization: Organization, user_client: APIClient, published: Any
    ) -> None:
        """A member may recover a failed task."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)
        _fail(task_run_id)
        member = _client_for(organization, Membership.Role.MEMBER, "member")

        response = member.post(reverse("task-run-requeue", kwargs={"pk": task_run_id}))

        assert response.status_code == status.HTTP_200_OK

    def test_an_unauthenticated_caller_gets_401_on_every_endpoint(
        self, api_client: APIClient, published: Any
    ) -> None:
        """No credentials is always 401, never 403."""
        for url in (
            reverse("workflow-list"),
            reverse("run-list"),
            reverse("api-key-list"),
        ):
            assert api_client.get(url).status_code == status.HTTP_401_UNAUTHORIZED
