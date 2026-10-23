"""The API-key service: hashing, lookup, rotation and revocation.

These are the properties the API layer relies on, tested at the service so a
failure is attributed to the mechanism rather than to a view.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from core.models import ApiKey, Organization
from core.services.api_keys import (
    authenticate_api_key,
    create_api_key,
    revoke_api_key,
    rotate_api_key,
)
from django.utils import timezone


@pytest.mark.django_db
class TestCreate:
    """Creation returns the plaintext once and stores a hash."""

    def test_the_secret_is_not_stored(self, organization: Organization) -> None:
        """A database reader cannot recover the key."""
        api_key, raw = create_api_key(organization=organization, name="ci", scopes=["read"])

        assert raw.startswith("ff_")
        assert api_key.hashed_key != raw
        assert raw.split("_", 2)[2] not in api_key.hashed_key

    def test_the_prefix_is_unique(self, organization: Organization) -> None:
        """Two keys never share a lookup handle."""
        first, _ = create_api_key(organization=organization, name="a", scopes=["read"])
        second, _ = create_api_key(organization=organization, name="b", scopes=["read"])

        assert first.prefix != second.prefix

    def test_an_expiry_is_recorded(self, organization: Organization) -> None:
        """A key may be issued with a deadline."""
        moment = timezone.now() + timedelta(days=1)
        api_key, _ = create_api_key(
            organization=organization, name="ci", scopes=["read"], expires_at=moment
        )

        assert api_key.expires_at == moment


@pytest.mark.django_db
class TestAuthenticate:
    """Lookup by prefix, verification by hash, expiry and revocation."""

    def test_a_valid_key_authenticates(self, organization: Organization) -> None:
        """The plaintext maps back to its row."""
        api_key, raw = create_api_key(organization=organization, name="ci", scopes=["read"])

        assert authenticate_api_key(raw) == api_key

    def test_a_wrong_secret_is_rejected(self, organization: Organization) -> None:
        """Knowing the prefix is not enough."""
        api_key, _raw = create_api_key(organization=organization, name="ci", scopes=["read"])

        assert authenticate_api_key(f"ff_{api_key.prefix}_{'x' * 43}") is None

    def test_a_malformed_string_is_rejected(self, organization: Organization) -> None:
        """Anything not in the issued shape is refused without a lookup."""
        assert authenticate_api_key("nope") is None
        assert authenticate_api_key("ff_only") is None
        assert authenticate_api_key("xx_prefix_secret") is None

    def test_an_expired_key_is_rejected(self, organization: Organization) -> None:
        """Expiry is checked at authentication time."""
        api_key, raw = create_api_key(
            organization=organization,
            name="ci",
            scopes=["read"],
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        assert api_key.is_usable() is False
        assert authenticate_api_key(raw) is None

    def test_a_revoked_key_is_rejected(self, organization: Organization) -> None:
        """Revocation is immediate."""
        api_key, raw = create_api_key(organization=organization, name="ci", scopes=["read"])
        revoke_api_key(api_key=api_key)

        assert authenticate_api_key(raw) is None


@pytest.mark.django_db
class TestRotate:
    """Rotation revokes the old key and issues a working replacement."""

    def test_rotation_invalidates_the_old_key(self, organization: Organization) -> None:
        """The old plaintext stops authenticating."""
        old, old_raw = create_api_key(organization=organization, name="ci", scopes=["read"])

        new, new_raw = rotate_api_key(api_key=old)

        old.refresh_from_db()
        assert old.revoked_at is not None
        assert authenticate_api_key(old_raw) is None
        assert authenticate_api_key(new_raw) == new

    def test_rotation_copies_the_key_properties(self, organization: Organization) -> None:
        """The replacement keeps the name, scopes and expiry."""
        old, _ = create_api_key(organization=organization, name="ci", scopes=["write"])

        new, _ = rotate_api_key(api_key=old)

        assert new.name == old.name
        assert new.scopes == old.scopes
        assert new.expires_at == old.expires_at


@pytest.mark.django_db
class TestRevoke:
    """Revocation is durable and idempotent."""

    def test_revoking_twice_keeps_the_first_timestamp(self, organization: Organization) -> None:
        """A second revoke must not rewrite history."""
        api_key, _ = create_api_key(organization=organization, name="ci", scopes=["read"])
        revoke_api_key(api_key=api_key)
        first = api_key.revoked_at

        revoke_api_key(api_key=api_key)

        assert api_key.revoked_at == first


class TestScopeHierarchy:
    """``admin`` implies ``write`` implies ``read``."""

    def test_admin_implies_everything(self) -> None:
        """The broadest scope satisfies every requirement."""
        key = ApiKey(scopes=["admin"])
        assert key.has_scope("read") and key.has_scope("write") and key.has_scope("admin")

    def test_write_does_not_imply_admin(self) -> None:
        """Scopes are not symmetric."""
        key = ApiKey(scopes=["write"])
        assert key.has_scope("read") and key.has_scope("write")
        assert not key.has_scope("admin")

    def test_read_is_the_narrowest(self) -> None:
        """A read key cannot write."""
        key = ApiKey(scopes=["read"])
        assert key.has_scope("read")
        assert not key.has_scope("write")
