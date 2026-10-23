"""Issuing, authenticating, rotating and revoking API keys.

The properties asserted here are the security contract:

* the plaintext key is returned exactly once and never stored;
* listing exposes metadata only;
* a key authenticates as its creator, in the key's organization;
* an expired, revoked, orphaned or wrong-scope key is refused;
* rotation invalidates the old key before the new one is usable;
* a key in one tenant cannot read another tenant's data.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from core.models import ApiKey, Membership
from core.services.api_keys import create_api_key
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

LIST_URL = "api-key-list"


def _create(client: APIClient, **body: Any) -> Any:
    """Create a key through the API."""
    payload = {"name": "ci", "scopes": ["read"], **body}
    return client.post(reverse(LIST_URL), payload, format="json")


def _as_key(raw: str) -> APIClient:
    """A client presenting ``raw`` as its API key."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw}")
    return client


@pytest.mark.django_db
class TestApiKeyCreation:
    """Creating a key returns the plaintext once and stores only a hash."""

    def test_creation_returns_the_plaintext_once(self, staff_client: APIClient) -> None:
        """The one response that carries the secret."""
        response = _create(staff_client)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["key"].startswith("ff_")
        assert response.data["scopes"] == ["read"]
        assert "hashed_key" not in response.data

    def test_only_a_hash_is_stored(self, staff_client: APIClient) -> None:
        """A database dump must not be replayable."""
        response = _create(staff_client)
        raw = response.data["key"]

        stored = ApiKey.objects.get(prefix=response.data["prefix"])
        assert stored.hashed_key != raw
        assert raw not in stored.hashed_key

    def test_listing_never_returns_a_secret(self, staff_client: APIClient) -> None:
        """Listing is metadata only."""
        _create(staff_client)

        results = staff_client.get(reverse(LIST_URL)).data

        assert results
        assert "key" not in results[0]
        assert "hashed_key" not in results[0]

    def test_a_member_cannot_create_a_key(self, user_client: APIClient) -> None:
        """Issuing a credential is an admin action."""
        assert _create(user_client).status_code == status.HTTP_403_FORBIDDEN

    def test_an_unauthenticated_caller_cannot_create_a_key(self, api_client: APIClient) -> None:
        """No credentials, no key."""
        assert _create(api_client).status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestApiKeyAuthentication:
    """A key authenticates as its creator, in the key's organization."""

    def test_a_valid_key_authenticates(self, staff_client: APIClient, published: Any) -> None:
        """The key can read the organization's runs."""
        raw = _create(staff_client).data["key"]

        response = _as_key(raw).get(reverse("run-list"))

        assert response.status_code == status.HTTP_200_OK

    def test_an_expired_key_is_rejected(self, staff_client: APIClient) -> None:
        """Expiry is enforced at authentication, not only at creation."""
        raw = _create(
            staff_client,
            expires_at=(timezone.now() - timedelta(minutes=1)).isoformat(),
        ).data["key"]

        response = _as_key(raw).get(reverse("run-list"))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED, response.data

    def test_a_revoked_key_is_rejected(self, staff_client: APIClient) -> None:
        """Revocation takes effect immediately."""
        created = _create(staff_client)
        raw = created.data["key"]
        staff_client.post(reverse("api-key-revoke", kwargs={"pk": created.data["id"]}))

        assert _as_key(raw).get(reverse("run-list")).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_key_without_the_write_scope_cannot_trigger(
        self, staff_client: APIClient, published: Any
    ) -> None:
        """A read key must not start work, even when its creator could."""
        raw = _create(staff_client, scopes=["read"]).data["key"]

        response = _as_key(raw).post(
            reverse("run-list"), {"name": "etl", "version": 1}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_write_scope_key_can_trigger(self, staff_client: APIClient, published: Any) -> None:
        """The scope is what unlocks the action."""
        raw = _create(staff_client, scopes=["write"]).data["key"]

        response = _as_key(raw).post(
            reverse("run-list"), {"name": "etl", "version": 1}, format="json"
        )

        assert response.status_code == status.HTTP_202_ACCEPTED

    def test_an_orphaned_key_is_rejected(self, user: Any, organization: Any) -> None:
        """A removed member's credential must stop working."""
        _api_key, raw = create_api_key(
            organization=organization, name="orphan", scopes=["read"], created_by=user
        )
        Membership.objects.filter(user=user, organization=organization).delete()

        assert _as_key(raw).get(reverse("run-list")).status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestApiKeyRotation:
    """Rotation revokes the old key before the new one is usable."""

    def test_rotation_invalidates_the_old_key(self, staff_client: APIClient) -> None:
        """The old credential must stop working the moment it is rotated."""
        created = _create(staff_client)
        old = created.data["key"]

        rotated = staff_client.post(reverse("api-key-rotate", kwargs={"pk": created.data["id"]}))

        assert rotated.status_code == status.HTTP_200_OK
        new = rotated.data["key"]
        assert new != old
        assert _as_key(old).get(reverse("run-list")).status_code == status.HTTP_401_UNAUTHORIZED
        assert _as_key(new).get(reverse("run-list")).status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestApiKeyIsolation:
    """A key is bound to its organization and cannot cross tenants."""

    def test_a_key_cannot_read_another_tenants_run(
        self,
        user_client: APIClient,
        stranger: Any,
        other_organization: Any,
        published: Any,
    ) -> None:
        """The run detail is scoped to the key's organization."""
        created = user_client.post(
            reverse("run-list"), {"name": "etl", "version": 1}, format="json"
        )
        run_id = created.data["id"]

        _api_key, raw = create_api_key(
            organization=other_organization,
            name="other",
            scopes=["read"],
            created_by=stranger,
        )

        response = _as_key(raw).get(reverse("run-detail", kwargs={"pk": run_id}))

        assert response.status_code == status.HTTP_404_NOT_FOUND
