"""The permission classes, called directly.

These are unit tests rather than API tests because two of the branches are
unreachable through the HTTP surface: the object-level guard for an anonymous
caller is pre-empted by `IsAuthenticatedAndActive`, and an object with no owner
cannot be produced by any current view. They exist as defences against a second
view using a rule without its companion, so they are tested where they live.

The user objects are stand-ins rather than database rows: the classes read
exactly four attributes, and using a real `User` would make these tests about
Django's auth model instead of about the rules.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from api.permissions import IsAuthenticatedAndActive, IsOwnerOrStaff, IsStaffOrReadOnly
from django.contrib.auth.models import AnonymousUser


def _request(*, user: Any, method: str = "GET") -> Any:
    """The two request attributes the permission classes read."""
    return SimpleNamespace(user=user, method=method)


def _user(*, pk: int = 1, is_staff: bool = False, is_active: bool = True) -> Any:
    """An authenticated user stand-in."""
    return SimpleNamespace(pk=pk, is_staff=is_staff, is_active=is_active, is_authenticated=True)


def _obj(*, owner_id: int | None = 1) -> Any:
    """An object stand-in, with or without an owner."""
    if owner_id is None:
        return SimpleNamespace()
    return SimpleNamespace(requested_by_id=owner_id)


class TestIsAuthenticatedAndActive:
    """Authenticated is not enough; a deactivated account keeps its token."""

    def test_an_anonymous_caller_is_refused(self) -> None:
        """No user at all."""
        assert (
            IsAuthenticatedAndActive().has_permission(_request(user=AnonymousUser()), None) is False
        )

    def test_a_deactivated_caller_is_refused(self) -> None:
        """The reason this class exists rather than `IsAuthenticated`."""
        assert (
            IsAuthenticatedAndActive().has_permission(_request(user=_user(is_active=False)), None)
            is False
        )

    def test_an_active_caller_is_allowed(self) -> None:
        """Both flags set is the only allowed case."""
        assert IsAuthenticatedAndActive().has_permission(_request(user=_user()), None) is True


class TestIsStaffOrReadOnly:
    """Reading is open to any active caller; writing is staff-only."""

    def test_a_non_staff_caller_may_read(self) -> None:
        """A definition is not a secret."""
        assert IsStaffOrReadOnly().has_permission(_request(user=_user()), None) is True

    def test_a_non_staff_caller_may_not_write(self) -> None:
        """Publishing changes what every future run executes."""
        assert (
            IsStaffOrReadOnly().has_permission(_request(user=_user(), method="POST"), None) is False
        )

    def test_staff_may_write(self) -> None:
        """The one write path that is allowed."""
        assert (
            IsStaffOrReadOnly().has_permission(
                _request(user=_user(is_staff=True), method="POST"), None
            )
            is True
        )

    def test_an_anonymous_caller_may_not_read(self) -> None:
        """Read-only does not mean public."""
        assert IsStaffOrReadOnly().has_permission(_request(user=AnonymousUser()), None) is False


class TestIsOwnerOrStaff:
    """Object-level: the owner, or staff, and nobody else."""

    def test_the_owner_may_read(self) -> None:
        """Ownership is the ordinary case."""
        assert (
            IsOwnerOrStaff().has_object_permission(
                _request(user=_user(pk=7)), None, _obj(owner_id=7)
            )
            is True
        )

    def test_a_non_owner_may_not_read(self) -> None:
        """403 rather than 404: the row exists and the caller is not allowed."""
        assert (
            IsOwnerOrStaff().has_object_permission(
                _request(user=_user(pk=7)), None, _obj(owner_id=8)
            )
            is False
        )

    def test_staff_may_read_any_object(self) -> None:
        """Operators need the whole picture."""
        assert (
            IsOwnerOrStaff().has_object_permission(
                _request(user=_user(pk=7, is_staff=True)), None, _obj(owner_id=8)
            )
            is True
        )

    def test_an_unauthenticated_caller_is_refused(self) -> None:
        """Unreachable through the API today; kept so a second view cannot skip it."""
        assert (
            IsOwnerOrStaff().has_object_permission(
                _request(user=AnonymousUser()), None, _obj(owner_id=7)
            )
            is False
        )

    def test_an_object_with_no_owner_is_refused(self) -> None:
        """A system-started run has `requested_by = NULL` and belongs to nobody."""
        assert (
            IsOwnerOrStaff().has_object_permission(
                _request(user=_user(pk=7)), None, _obj(owner_id=None)
            )
            is False
        )
