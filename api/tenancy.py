"""Resolving the active organization for a request.

Every tenant-scoped endpoint needs to know *which tenant* the caller is acting
as. That is not a property of the user — a user may belong to several
organizations with a different role in each — so it is resolved per request:

1. **An API key wins.** A key is issued by, and belongs to, exactly one
   organization; using it selects that organization (see ``api.authentication``).
2. **An explicit header.** ``X-Organization: <slug or uuid>`` selects an
   organization the caller is a member of. This is how a user who belongs to
   several organizations disambiguates.
3. **A single membership.** With exactly one membership and no header, the choice
   is unambiguous and is made for the caller.

Anything else — no membership, a header naming an organization the caller is not
a member of, or several memberships with no header — resolves to ``None``, which
the permission layer turns into a 403. Silence is never treated as consent: a
request that cannot be attributed to a tenant must not read tenant data.

The result is cached on the request object because a single request resolves it
more than once (the permission class, then the view's queryset).
"""

from __future__ import annotations

import uuid
from typing import cast

from core.models import Membership, Organization
from rest_framework.exceptions import PermissionDenied

#: The header a multi-organization caller uses to choose the active tenant.
ORG_HEADER = "X-Organization"

#: Attribute used to cache the resolved membership on the request object.
_CACHE_ATTR = "_flowforge_membership"


def _organization_from_header(value: str) -> Organization | None:
    """Resolve an organization from a slug (preferred) or a UUID.

    A slug is the readable choice and the one the API documents; a UUID is
    accepted because a client that already holds an id should not have to look
    the slug up first.
    """
    organization = Organization.objects.filter(slug=value).first()
    if organization is not None:
        return organization
    try:
        organization_id = uuid.UUID(value)
    except ValueError:
        return None
    return Organization.objects.filter(pk=organization_id).first()


def resolve_membership(request: object) -> Membership | None:
    """The caller's membership in the active organization, or ``None``.

    Returns ``None`` when the caller is anonymous, belongs to no organization, or
    asked for an organization they are not a member of. The caller decides
    whether that is a 403; this function never raises.
    """
    if hasattr(request, _CACHE_ATTR):
        return cast("Membership | None", getattr(request, _CACHE_ATTR))

    membership: Membership | None = None
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        memberships = Membership.objects.filter(user=user).select_related("organization")
        header = _header(request)
        if header:
            organization = _organization_from_header(header)
            if organization is not None:
                membership = memberships.filter(organization=organization).first()
        else:
            candidates = list(memberships[:2])
            if len(candidates) == 1:
                membership = candidates[0]

    setattr(request, _CACHE_ATTR, membership)
    return membership


def _header(request: object) -> str | None:
    """The ``X-Organization`` header, or ``None`` when absent or blank."""
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    value = headers.get(ORG_HEADER)
    return value.strip() if value and value.strip() else None


def get_active_organization(request: object) -> Organization | None:
    """The organization the caller is acting as, or ``None``."""
    membership = resolve_membership(request)
    return membership.organization if membership is not None else None


def require_active_organization(request: object) -> Organization:
    """The active organization, or a 403 explaining how to choose one.

    Used by write paths that must be attributed to a tenant (publishing,
    triggering, key management). A caller who belongs to several organizations
    gets a message naming the header rather than a silent choice.
    """
    organization = get_active_organization(request)
    if organization is None:
        raise PermissionDenied(
            "No active organization. Send an 'X-Organization' header naming an "
            "organization you belong to."
        )
    return organization
