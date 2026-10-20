"""Fixtures for the API tests.

Authentication is a fixture rather than a helper called in each test because
every test needs to say *who* is calling, and a token created once per test is
what makes the permission tests about permissions rather than about setup.
"""

from __future__ import annotations

import pytest
from core.domain.schema import parse_workflow_definition
from core.models import Workflow
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
def user(db: None) -> User:
    """An ordinary active account."""
    return User.objects.create_user("alice", password="pw12345!")


@pytest.fixture
def other_user(db: None) -> User:
    """A second ordinary account, for ownership tests."""
    return User.objects.create_user("bob", password="pw12345!")


@pytest.fixture
def staff_user(db: None) -> User:
    """An account allowed to publish definitions."""
    return User.objects.create_user("root", password="pw12345!", is_staff=True)


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
def published(db: None) -> Workflow:
    """A published ``etl`` v1 definition, written through the domain path.

    Setup goes through the service rather than the HTTP API on purpose: a run
    test that fails because publishing broke would report the wrong defect.
    """
    workflow, _ = publish_definition(parse_workflow_definition(definition_payload()))
    return workflow
