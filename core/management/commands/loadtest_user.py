"""Create (idempotently) the load-test user and print its credentials.

The load test in ``loadtests/locustfile.py`` authenticates with these. Like
``e2e_token`` this is a fixture, not a production command: the password is fixed
and deliberately weak, and the account has no privileges beyond being able to
publish and trigger in its own organization.

The credentials are printed rather than written to a file so they end up in the
operator's terminal, not in a temp file a log line could pick up.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token

from core.models import Membership
from core.services.tenancy import ensure_default_organization

USERNAME = "loadtest"
PASSWORD = "loadtest-password"  # noqa: S105  # nosec B105 - fixture, not a credential


class Command(BaseCommand):
    """Create the load-test user and show the credentials Locust needs."""

    help = "Create the load-test user and print the credentials Locust needs."

    def handle(self, *_args: object, **_options: object) -> None:
        """Create the user, mint a token, and print the credentials."""
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=USERNAME)
        user.is_active = True
        user.set_password(PASSWORD)
        user.save()

        # Owner in its own organization, so the load test can publish and trigger
        # without touching any other tenant's data. Staff is deliberately not
        # set: the load test should exercise the ordinary permission path.
        Membership.objects.get_or_create(
            organization=ensure_default_organization(),
            user=user,
            defaults={"role": Membership.Role.OWNER},
        )
        Token.objects.get_or_create(user=user)

        verb = "Created" if created else "Updated"
        self.stdout.write(f"{verb} load-test user {USERNAME!r}.")
        self.stdout.write(f"  username: {USERNAME}")
        self.stdout.write(f"  password: {PASSWORD}")
        self.stdout.write(
            "Use with LOCUST_USERNAME / LOCUST_PASSWORD; see docs/development/load-testing.md."
        )
