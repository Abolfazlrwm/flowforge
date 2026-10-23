"""Fixtures for the API tests.

Authentication is a fixture rather than a helper called in each test because
every test needs to say *who* is calling, and a token created once per test is
what makes the permission tests about permissions rather than about setup.

Tenancy is a fixture for the same reason: authorization is now per organization,
so every test also has to say *which tenant* the caller acts as. The defaults
give every client a membership in one shared organization, which is what a
same-tenant test wants. ``stranger_client`` is deliberately in a *different*
organization: it is the caller every isolation test needs, and keeping it a
separate fixture stops a test from accidentally proving same-tenant behaviour
when it meant to prove isolation.
"""

from __future__ import annotations

import pytest
from core.domain.schema import parse_workflow_definition
from core.models import Membership, Organization, Workflow
from core.repositories.definitions import publish_definition
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from tests.unit.api.payloads import definition_payload


def _authenticate(user: User) -> APIClient:
    """A client carrying a DRF token for ``user``."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    return client


@pytest.fixture
def organization(db: None) -> Organization:
    """The tenant every default client belongs to."""
    return Organization.objects.create(name="Acme", slug="acme")


@pytest.fixture
def other_organization(db: None) -> Organization:
    """A second tenant, used by the isolation tests."""
    return Organization.objects.create(name="Globex", slug="globex")


@pytest.fixture
def user(db: None, organization: Organization) -> User:
    """An ordinary active account with the ``member`` role in ``organization``."""
    account = User.objects.create_user("alice", password="pw12345!")
    Membership.objects.create(organization=organization, user=account, role=Membership.Role.MEMBER)
    return account


@pytest.fixture
def other_user(db: None, organization: Organization) -> User:
    """A second account in the *same* organization as :func:`user`."""
    account = User.objects.create_user("bob", password="pw12345!")
    Membership.objects.create(organization=organization, user=account, role=Membership.Role.MEMBER)
    return account


@pytest.fixture
def staff_user(db: None, organization: Organization) -> User:
    """A platform operator: staff, and an owner of the default organization."""
    account = User.objects.create_user("root", password="pw12345!", is_staff=True)
    Membership.objects.create(organization=organization, user=account, role=Membership.Role.OWNER)
    return account


@pytest.fixture
def stranger(db: None, other_organization: Organization) -> User:
    """A member of a *different* organization — the isolation-test caller."""
    account = User.objects.create_user("eve", password="pw12345!")
    Membership.objects.create(
        organization=other_organization, user=account, role=Membership.Role.MEMBER
    )
    return account


@pytest.fixture
def user_client(user: User) -> APIClient:
    """An authenticated client for :func:`user`."""
    return _authenticate(user)


@pytest.fixture
def other_client(other_user: User) -> APIClient:
    """An authenticated client for :func:`other_user`."""
    return _authenticate(other_user)


@pytest.fixture
def staff_client(staff_user: User) -> APIClient:
    """An authenticated client for :func:`staff_user`."""
    return _authenticate(staff_user)


@pytest.fixture
def stranger_client(stranger: User) -> APIClient:
    """An authenticated client for a member of another organization."""
    return _authenticate(stranger)


@pytest.fixture
def published(db: None, organization: Organization) -> Workflow:
    """A published ``etl`` v1 definition in the default organization.

    Setup goes through the service rather than the HTTP API on purpose: a run
    test that fails because publishing broke would report the wrong defect.
    """
    workflow, _ = publish_definition(
        parse_workflow_definition(definition_payload()), organization=organization
    )
    return workflow
