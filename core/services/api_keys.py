"""Issuing, rotating and verifying organization-scoped API keys.

The security properties, in the order they matter:

1. **The secret is never stored.** Only ``make_password(secret)`` is persisted,
   so a database dump cannot be replayed against the API. ``check_password`` is
   what makes verification constant-time against the stored hash.
2. **Lookup does not require hashing every row.** The key is presented as
   ``ff_<prefix>_<secret>``; ``prefix`` is a random, non-secret handle stored in
   the clear and indexed. The row is found by prefix, then the secret is verified
   against that one hash.
3. **A key is scoped to one organization.** It cannot be used to read or write
   another tenant, and the caller's active organization is the key's.
4. **Revocation is durable.** Revoking sets ``revoked_at`` rather than deleting
   the row, so the credential's existence stays auditable.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from core.models import ApiKey, Organization

#: Prefix of every issued key. Recognisable in logs without revealing the secret.
KEY_PREFIX = "ff"

#: Entropy of the random parts. ``token_hex(8)`` is 16 hex characters and
#: ``token_hex(32)`` is 64 — together far beyond brute-force range.
#:
#: Hex, not ``token_urlsafe``: the key is ``ff_<prefix>_<secret>`` and the parser
#: splits on ``_``. The URL-safe alphabet contains ``_``, so a prefix could
#: itself contain the separator and make the key unparseable. Hex cannot.
_PREFIX_BYTES = 8
_SECRET_BYTES = 32


def _new_prefix() -> str:
    """A random, collision-checked lookup handle (hex, so no ``_``)."""
    for _ in range(10):
        candidate = secrets.token_hex(_PREFIX_BYTES)
        if not ApiKey.objects.filter(prefix=candidate).exists():
            return candidate
    raise RuntimeError("could not allocate a unique API-key prefix")


def create_api_key(
    *,
    organization: Organization,
    name: str,
    scopes: list[str],
    created_by: Any = None,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    """Create a key and return it with its **plaintext** value.

    The plaintext is returned exactly once, here. Nothing else in the codebase
    can recover it; a caller that loses it must rotate.
    """
    prefix = _new_prefix()
    secret = secrets.token_hex(_SECRET_BYTES)
    api_key = ApiKey.objects.create(
        organization=organization,
        name=name,
        prefix=prefix,
        hashed_key=make_password(secret),
        scopes=list(scopes),
        created_by=created_by,
        expires_at=expires_at,
    )
    return api_key, f"{KEY_PREFIX}_{prefix}_{secret}"


def rotate_api_key(*, api_key: ApiKey, now: datetime | None = None) -> tuple[ApiKey, str]:
    """Revoke ``api_key`` and issue a replacement with the same properties.

    Rotation is atomic with respect to the old key: the old row is marked
    revoked before the new one is returned, so there is no window in which both
    are usable.
    """
    revoke_api_key(api_key=api_key, now=now)
    return create_api_key(
        organization=api_key.organization,
        name=api_key.name,
        scopes=list(api_key.scopes),
        created_by=api_key.created_by,
        expires_at=api_key.expires_at,
    )


def revoke_api_key(*, api_key: ApiKey, now: datetime | None = None) -> None:
    """Mark ``api_key`` revoked. Idempotent."""
    if api_key.revoked_at is not None:
        return
    api_key.revoked_at = now if now is not None else timezone.now()
    api_key.save(update_fields=["revoked_at"])


def authenticate_api_key(raw: str, *, now: datetime | None = None) -> ApiKey | None:
    """Return the key a presented string names, or ``None``.

    ``None`` is returned for a malformed string, an unknown prefix, a wrong
    secret, an expired key and a revoked key — deliberately indistinguishable, so
    a caller cannot use the response to enumerate which prefixes exist.
    """
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    _scheme, prefix, secret = parts
    api_key = (
        ApiKey.objects.select_related("organization", "created_by").filter(prefix=prefix).first()
    )
    if api_key is None:
        return None
    if not check_password(secret, api_key.hashed_key):
        return None
    if not api_key.is_usable(now=now):
        return None
    return api_key
