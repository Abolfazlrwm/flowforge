"""Permission classes, one per rule, each named for what it allows.

Every view names its permission class explicitly. There is a default in settings
(`IsAuthenticated`) so that a view which forgets cannot become public by
accident, but a view that relies on the default without saying so is a view whose
author never made a decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request

if TYPE_CHECKING:
    # Imported for the type only. `rest_framework.views` reads DRF settings at
    # import time, and those settings name this module as the default permission
    # class — importing it for real is a circular import that only shows up when
    # this module happens to be imported first.
    from rest_framework.views import APIView


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


class IsStaffOrReadOnly(BasePermission):
    """Anyone authenticated may read; only staff may write.

    Publishing a definition changes what every future run executes, so it is not
    an ordinary authenticated write.
    """

    message = "Only staff may modify this resource."

    def has_permission(self, request: Request, view: APIView) -> bool:
        """Read only, unless the caller is staff."""
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        if request.method in SAFE_METHODS:
            return True
        return bool(user.is_staff)


class IsOwnerOrStaff(BasePermission):
    """Object-level: the requester owns the row, or is staff.

    Runs are per-user. A run started by someone else must not be readable, and
    "not readable" is a 403 rather than a 404 because the row demonstrably
    exists — hiding it would be a second, contradictory policy.
    """

    message = "You do not have permission to access this resource."

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        """The owner, or any staff member."""
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_staff:
            return True
        owner_id = getattr(obj, "requested_by_id", None)
        return owner_id is not None and owner_id == user.pk
