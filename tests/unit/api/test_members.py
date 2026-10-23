"""Membership management: RBAC, the last-owner rule, isolation and audit.

The rules under test are the ones that keep an organization administrable:

* only an owner may grant or modify the owner role;
* the last owner cannot be removed or demoted;
* an admin cannot touch an owner;
* a membership in another tenant is a 404, not a 403;
* every real change writes an audit entry.
"""

from __future__ import annotations

import pytest
from core.models import AuditLog, Membership, Organization
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

LIST_URL = "member-list"


def _member_client(organization: Organization, role: str, label: str) -> tuple[User, APIClient]:
    """A non-staff user with ``role`` in ``organization`` and a token client."""
    user = User.objects.create_user(label, password="pw12345!")
    Membership.objects.create(organization=organization, user=user, role=role)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    return user, client


def _membership(organization: Organization, user: User) -> Membership:
    """The membership row for ``user`` in ``organization``."""
    return Membership.objects.get(organization=organization, user=user)


def _detail_url(membership: Membership) -> str:
    """The canonical URL of a membership."""
    return reverse("member-detail", kwargs={"pk": membership.pk})


@pytest.mark.django_db
class TestList:
    """Only admins and owners may see the member list."""

    def test_a_member_cannot_list_members(self, user_client: APIClient) -> None:
        """The list exposes usernames and e-mails."""
        assert user_client.get(reverse(LIST_URL)).status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_can_list_members(self, organization: Organization, user: User) -> None:
        """An admin sees every member of the tenant."""
        _admin, client = _member_client(organization, Membership.Role.ADMIN, "admin")

        response = client.get(reverse(LIST_URL))

        assert response.status_code == status.HTTP_200_OK
        usernames = {row["username"] for row in response.data}
        assert "admin" in usernames
        assert user.username in usernames


@pytest.mark.django_db
class TestAdd:
    """Adding a member, and who may grant which role."""

    def test_an_owner_adds_a_member(self, organization: Organization, db: None) -> None:
        """201, the requested role, and an audit entry."""
        _owner, client = _member_client(organization, Membership.Role.OWNER, "owner")
        target = User.objects.create_user("newcomer", password="pw12345!")

        response = client.post(
            reverse(LIST_URL),
            {"username": "newcomer", "role": "viewer"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["username"] == "newcomer"
        assert response.data["role"] == "viewer"
        entry = AuditLog.objects.get(action="member.added")
        assert entry.metadata["username"] == target.username

    def test_adding_an_existing_member_is_409(
        self, organization: Organization, other_user: User, db: None
    ) -> None:
        """The unique (organization, user) constraint is reported as a conflict."""
        _owner, client = _member_client(organization, Membership.Role.OWNER, "owner")

        response = client.post(
            reverse(LIST_URL),
            {"username": other_user.username, "role": "viewer"},
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_adding_an_unknown_user_is_404(self, organization: Organization, db: None) -> None:
        """A username nobody holds is not a member to add."""
        _owner, client = _member_client(organization, Membership.Role.OWNER, "owner")

        response = client.post(
            reverse(LIST_URL), {"username": "ghost", "role": "viewer"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_admin_cannot_grant_owner(self, organization: Organization, db: None) -> None:
        """Granting owner is a privilege escalation an admin must not perform."""
        _admin, client = _member_client(organization, Membership.Role.ADMIN, "admin")
        User.objects.create_user("target", password="pw12345!")

        response = client.post(
            reverse(LIST_URL), {"username": "target", "role": "owner"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_owner_can_grant_owner(self, organization: Organization, db: None) -> None:
        """An owner may appoint another owner."""
        _owner, client = _member_client(organization, Membership.Role.OWNER, "owner")
        User.objects.create_user("target", password="pw12345!")

        response = client.post(
            reverse(LIST_URL), {"username": "target", "role": "owner"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["role"] == "owner"


@pytest.mark.django_db
class TestChangeRole:
    """Changing a role, including the owner protections."""

    def test_an_owner_promotes_a_member(self, organization: Organization, db: None) -> None:
        """200, the new role, and an audit entry with the transition."""
        member, _ = _member_client(organization, Membership.Role.MEMBER, "member")
        _owner, client = _member_client(organization, Membership.Role.OWNER, "owner")

        response = client.patch(
            _detail_url(_membership(organization, member)), {"role": "admin"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["role"] == "admin"
        entry = AuditLog.objects.get(action="member.role_changed")
        assert entry.metadata["from"] == "member"
        assert entry.metadata["to"] == "admin"

    def test_an_admin_cannot_modify_an_owner(self, organization: Organization, db: None) -> None:
        """An admin must not demote the owner who appointed them."""
        owner, _ = _member_client(organization, Membership.Role.OWNER, "owner")
        _admin, client = _member_client(organization, Membership.Role.ADMIN, "admin")

        response = client.patch(
            _detail_url(_membership(organization, owner)), {"role": "member"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_last_owner_cannot_be_demoted(self, organization: Organization, db: None) -> None:
        """The only owner is what keeps the organization administrable."""
        owner, client = _member_client(organization, Membership.Role.OWNER, "solo-owner")

        response = client.patch(
            _detail_url(_membership(organization, owner)), {"role": "admin"}, format="json"
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_an_owner_can_be_demoted_when_another_owner_exists(
        self, organization: Organization, db: None
    ) -> None:
        """Two owners is enough for one to step down."""
        owner_a, client_a = _member_client(organization, Membership.Role.OWNER, "owner-a")
        _member_client(organization, Membership.Role.OWNER, "owner-b")

        response = client_a.patch(
            _detail_url(_membership(organization, owner_a)), {"role": "admin"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK

    def test_changing_to_the_same_role_is_a_no_op(
        self, organization: Organization, db: None
    ) -> None:
        """Nothing changed, so nothing is audited."""
        member, _ = _member_client(organization, Membership.Role.MEMBER, "member")
        _owner, client = _member_client(organization, Membership.Role.OWNER, "owner")

        response = client.patch(
            _detail_url(_membership(organization, member)), {"role": "member"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert AuditLog.objects.filter(action="member.role_changed").count() == 0


@pytest.mark.django_db
class TestRemove:
    """Removing a member, including the owner protections."""

    def test_an_owner_removes_a_member(self, organization: Organization, db: None) -> None:
        """204, the row is gone, and the removal is audited."""
        member, _ = _member_client(organization, Membership.Role.MEMBER, "member")
        _owner, client = _member_client(organization, Membership.Role.OWNER, "owner")
        membership = _membership(organization, member)

        response = client.delete(_detail_url(membership))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Membership.objects.filter(pk=membership.pk).exists()
        entry = AuditLog.objects.get(action="member.removed")
        assert entry.metadata["username"] == "member"

    def test_an_admin_cannot_remove_an_owner(self, organization: Organization, db: None) -> None:
        """Same protection as demotion."""
        owner, _ = _member_client(organization, Membership.Role.OWNER, "owner")
        _admin, client = _member_client(organization, Membership.Role.ADMIN, "admin")

        response = client.delete(_detail_url(_membership(organization, owner)))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_last_owner_cannot_be_removed(self, organization: Organization, db: None) -> None:
        """An organization with no owner cannot be administered."""
        owner, client = _member_client(organization, Membership.Role.OWNER, "solo-owner")

        response = client.delete(_detail_url(_membership(organization, owner)))

        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.django_db
class TestIsolation:
    """Another tenant's membership is invisible."""

    def test_another_tenants_membership_is_404(
        self,
        organization: Organization,
        other_organization: Organization,
        user: User,
        db: None,
    ) -> None:
        """An admin of another tenant cannot even learn the row exists."""
        _other_admin, client = _member_client(
            other_organization, Membership.Role.ADMIN, "other-admin"
        )
        membership = _membership(organization, user)

        response = client.patch(_detail_url(membership), {"role": "admin"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
