"""Ensure the end-to-end user exists and write its DRF token to a file.

The compose CI job uses this to authenticate against the REST API. It is a test
fixture, not a production command: the password is fixed and the token is written
to the container's temp directory so a log line cannot contaminate it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from core.models import Membership
from core.services.tenancy import ensure_default_organization

USERNAME = "e2e"
PASSWORD = "e2e-password-not-for-production"  # noqa: S105 - test fixture
TOKEN_FILE = Path(tempfile.gettempdir()) / "e2e_token"


class Command(BaseCommand):
    """Create (idempotently) the e2e user and write its DRF token to a file."""

    help = "Create (idempotently) the e2e user and write its DRF token to a file."

    def handle(self, *_args: object, **_options: object) -> None:
        """Create the user, mint a token, and write it to the temp file."""
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(username=USERNAME)
        user.is_staff = True
        user.is_active = True
        user.set_password(PASSWORD)
        user.save()
        # The API is tenant-scoped, so the e2e caller must be a member of an
        # organization to publish and trigger. Staff bypasses the role check but
        # still needs an organization to attribute the definition to.
        Membership.objects.get_or_create(
            organization=ensure_default_organization(),
            user=user,
            defaults={"role": Membership.Role.OWNER},
        )
        token, _ = Token.objects.get_or_create(user=user)
        TOKEN_FILE.write_text(token.key)
        self.stdout.write(f"e2e token written to {TOKEN_FILE}")
