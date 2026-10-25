"""API-key authentication for the REST API.

A request carrying ``Authorization: Api-Key ff_...`` (or ``X-API-Key: ff_...``)
is authenticated as the key's creator, acting **as the key's organization**. The
active tenant is therefore fixed by the credential: a key can never be pointed
at another organization.

Returning ``None`` when no key header is present is what lets this class sit
alongside JWT and DRF-token auth: DRF tries each authenticator in order and uses
the first that returns a user, so an ordinary token request is unaffected.
"""

from __future__ import annotations

from typing import Any, cast

from core.models import ApiKey, Membership
from core.services.api_keys import authenticate_api_key
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request


class ApiKeyAuthentication(BaseAuthentication):
    """Authenticate ``Api-Key <key>`` / ``X-API-Key: <key>``."""

    keyword = "Api-Key"

    def authenticate(self, request: Request) -> tuple[object, ApiKey] | None:
        """Return ``(creator, key)``, or ``None`` when no key is presented.

        Raises:
            AuthenticationFailed: for a malformed, unknown, expired, revoked or
                orphaned key. The message is deliberately uniform so a caller
                cannot distinguish "unknown prefix" from "wrong secret".
        """
        raw = self._raw_key(request)
        if raw is None:
            return None
        api_key = authenticate_api_key(raw)
        if api_key is None or api_key.created_by is None:
            raise AuthenticationFailed("Invalid, expired, or revoked API key.")
        # A key must not outlive its creator's membership: a removed member's
        # credential would otherwise keep working until it expired.
        # Named locals rather than inline attribute access: the membership check
        # reads as "this user, this tenant", and it keeps the line short enough
        # that the condition is visible without scrolling.
        owner = api_key.created_by
        tenant = api_key.organization
        if not Membership.objects.filter(user=owner, organization=tenant).exists():
            raise AuthenticationFailed(
                "The API key's owner is no longer a member of its organization."
            )
        ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        # Fix the active tenant and the key on the request. `resolve_membership`
        # and `HasApiKeyScope` read these instead of re-deriving them. The cast
        # is only to satisfy DRF's `Request` stub, which has no such attributes.
        mutable = cast("Any", request)
        mutable.organization = api_key.organization
        mutable.api_key = api_key
        return (api_key.created_by, api_key)

    def authenticate_header(self, _request: Request) -> str:
        """Advertise the auth scheme.

        DRF downgrades a 401 to a 403 when the first authenticator returns no
        challenge header, so this is what keeps "bad key" a 401 rather than a
        misleading 403. It also keeps every unauthenticated request a 401, since
        this class is first in the authenticator list.
        """
        return f'{self.keyword} realm="api"'

    def _raw_key(self, request: Request) -> str | None:
        """The presented key, from either accepted header, or ``None``."""
        header = request.headers.get("Authorization", "")
        if header.startswith(f"{self.keyword} "):
            value = header.split(" ", 1)[1].strip()
            return value or None
        return request.headers.get("X-API-Key") or None
