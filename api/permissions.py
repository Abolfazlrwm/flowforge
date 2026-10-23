"""Permission classes, one per rule, each named for what it allows.

Authorization is **per organization**, not per user. A caller acts as a member of
exactly one active organization (see :mod:`api.tenancy`), and the role they hold
there decides what they may do. The four roles are ordered:

``viewer < member < admin < owner``

* **viewer** — read definitions, runs and event logs.
* **member** — viewer, plus triggering runs and re-running a failed task.
* **admin** — member, plus publishing definitions and managing API keys.
* **owner** — admin, plus managing members and the organization itself.

Global staff (``is_staff``) bypass the role check for operational access. That is
deliberate and narrow: staff are operators of the platform, not members of a
tenant, and the tenant-isolation suite is written against ordinary members so a
staff bypass can never hide a scoping bug.

A view that relies on the default permission without naming its own is a view
whose author never made a decision, so every view names its class explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from api.tenancy import resolve_membership

if TYPE_CHECKING:
    from rest_framework.views import APIView

#: Higher rank is more privileged. Unknown roles sort below ``viewer`` so a
#: corrupted row cannot accidentally grant access.
ROLE_RANK: dict[str, int] = {
    "viewer": 0,
    "member": 1,
    "admin": 2,
    "owner": 3,
}


class IsAuthenticatedAndActive(BasePermission):
    """Authenticated *and* not deactivated.

    DRF's ``IsAuthenticated`` checks ``is_authenticated`` only. A user whose
    account was deactivated still holds a valid token, so checking
    ``is_active`` here is what makes deactivation take effect immediately rather
    than at token expiry.
    """

    message = "Authentication credentials were not provided or are invalid."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Allow every active caller; read and write alike."""
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class HasOrganizationRole(BasePermission):
    """The caller holds at least ``minimum_role`` in the active organization.

    ``resolve_membership`` resolves the tenant from the API key, the
    ``X-Organization`` header, or a single membership. When it cannot, the caller
    is refused: a request that cannot be attributed to a tenant must not reach
    tenant data.
    """

    #: The least-privileged role that satisfies this permission.
    minimum_role: str = "viewer"
    message = "You do not have the required role in this organization."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Check the caller's role in the active organization."""
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        if user.is_staff:
            return True
        membership = resolve_membership(request)
        if membership is None:
            self.message = (
                "You do not belong to an organization. Send an 'X-Organization' "
                "header naming one you are a member of."
            )
            return False
        return ROLE_RANK.get(membership.role, -1) >= ROLE_RANK[self.minimum_role]


class IsViewer(HasOrganizationRole):
    """Any member of the organization, including read-only viewers."""

    minimum_role = "viewer"


class IsMember(HasOrganizationRole):
    """A member who may trigger work: viewer and above, excluding viewers."""

    minimum_role = "member"


class IsAdmin(HasOrganizationRole):
    """An administrator who may publish and manage keys."""

    minimum_role = "admin"


class IsOwner(HasOrganizationRole):
    """The organization owner."""

    minimum_role = "owner"


class HasApiKeyScope(BasePermission):
    """An API key must grant the scope its view requires.

    For session/token/JWT auth there is no key, so this class allows the request
    and the role class decides. For an API key, the view's ``required_scope``
    (``read`` / ``write`` / ``admin``) must be granted. The creator's role still
    applies as well — both checks run — so a key can never exceed its creator's
    authority even if it was issued with a broader scope.
    """

    message = "The API key does not grant the required scope."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Check the key's scope, if the request was authenticated by a key."""
        api_key = getattr(request, "api_key", None)
        if api_key is None:
            return True
        required = getattr(view, "required_scope", None)
        if required is None:
            return True
        return bool(api_key.has_scope(required))
