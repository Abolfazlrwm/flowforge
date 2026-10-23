"""The permission classes, called directly.

Authorization is per organization, so the role tests use real users and
memberships rather than stand-ins: the rule being tested *is* "which membership
does this request resolve to", and a stand-in would have to re-implement it.

``IsAuthenticatedAndActive`` is the exception — it reads four attributes and no
database, so it keeps its lightweight stand-ins.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from api.permissions import IsAdmin, IsAuthenticatedAndActive, IsMember, IsViewer
from core.models import Membership, Organization
from django.contrib.auth.models import AnonymousUser, User


def _request(*, user: Any, method: str = "GET", headers: dict[str, str] | None = None) -> Any:
    """The request attributes the permission classes read."""
    return SimpleNamespace(user=user, method=method, headers=headers or {})


def _user(*, pk: int = 1, is_staff: bool = False, is_active: bool = True) -> Any:
    """An authenticated user stand-in."""
    return SimpleNamespace(pk=pk, is_staff=is_staff, is_active=is_active, is_authenticated=True)


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


@pytest.mark.django_db
class TestRolePermissions:
    """The role in the active organization decides what a caller may do."""

    def _member(self, username: str, organization: Organization, role: str) -> User:
        """Create an active account with ``role`` in ``organization``."""
        account = User.objects.create_user(username, password="pw12345!")
        Membership.objects.create(organization=organization, user=account, role=role)
        return account

    def test_a_viewer_may_read(self, organization: Organization) -> None:
        """Reading is the viewer's whole job."""
        viewer = self._member("viewer", organization, Membership.Role.VIEWER)
        assert IsViewer().has_permission(_request(user=viewer), None) is True

    def test_a_viewer_may_not_trigger(self, organization: Organization) -> None:
        """A viewer must not start work."""
        viewer = self._member("viewer", organization, Membership.Role.VIEWER)
        assert IsMember().has_permission(_request(user=viewer), None) is False

    def test_a_member_may_trigger(self, organization: Organization) -> None:
        """Members run work."""
        member = self._member("member", organization, Membership.Role.MEMBER)
        assert IsMember().has_permission(_request(user=member), None) is True

    def test_a_member_may_not_publish(self, organization: Organization) -> None:
        """Publishing changes what every future run executes."""
        member = self._member("member", organization, Membership.Role.MEMBER)
        assert IsAdmin().has_permission(_request(user=member), None) is False

    def test_an_admin_may_publish(self, organization: Organization) -> None:
        """Administrators own the definition lifecycle."""
        admin = self._member("admin", organization, Membership.Role.ADMIN)
        assert IsAdmin().has_permission(_request(user=admin), None) is True

    def test_a_caller_with_no_membership_is_refused(self, db: None) -> None:
        """A user who belongs to no organization has no tenant to act in."""
        account = User.objects.create_user("nobody", password="pw12345!")
        permission = IsViewer()
        assert permission.has_permission(_request(user=account), None) is False
        assert "X-Organization" in permission.message

    def test_staff_bypass_the_role_check(self, db: None) -> None:
        """Staff are platform operators, not members of a tenant."""
        account = User.objects.create_user("root", password="pw12345!", is_staff=True)
        assert IsAdmin().has_permission(_request(user=account), None) is True

    def test_the_header_selects_between_memberships(self, db: None) -> None:
        """A user in two organizations must name one; the header is the choice."""
        acme = Organization.objects.create(name="Acme", slug="acme")
        globex = Organization.objects.create(name="Globex", slug="globex")
        account = User.objects.create_user("dual", password="pw12345!")
        Membership.objects.create(organization=acme, user=account, role=Membership.Role.VIEWER)
        Membership.objects.create(organization=globex, user=account, role=Membership.Role.ADMIN)

        # No header: ambiguous, so refused rather than silently choosing.
        assert IsAdmin().has_permission(_request(user=account), None) is False
        # Header names the tenant where the caller is an admin.
        assert (
            IsAdmin().has_permission(
                _request(user=account, headers={"X-Organization": "globex"}), None
            )
            is True
        )
        # The same header cannot grant the admin role in the other tenant.
        assert (
            IsAdmin().has_permission(
                _request(user=account, headers={"X-Organization": "acme"}), None
            )
            is False
        )

    def test_a_header_for_a_foreign_tenant_is_refused(self, db: None) -> None:
        """Naming an organization you do not belong to is not a membership."""
        Organization.objects.create(name="Acme", slug="acme")
        account = User.objects.create_user("outsider", password="pw12345!")
        assert (
            IsViewer().has_permission(
                _request(user=account, headers={"X-Organization": "acme"}), None
            )
            is False
        )
