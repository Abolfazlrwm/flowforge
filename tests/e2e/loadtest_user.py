"""Create (idempotently) the load-test user and print its credentials.

The load test in ``loadtests/locustfile.py`` authenticates with these. Like
``tests.e2e.provision`` this is a fixture, not a production surface: the
password is fixed and deliberately weak, and the account has no privileges
beyond being able to publish and trigger in its own organization.

The credentials are printed rather than written to a file so they end up in the
operator's terminal, not in a temp file a log line could pick up.
"""

from __future__ import annotations

USERNAME = "loadtest"
# A fixed, obviously-fake string for a fixture account that only exists in the
# throwaway load-test stack. The annotation says only the test id: bandit reads a
# trailing comment as more test names and warns about each word.
PASSWORD = "loadtest-password"  # nosec B105


def provision_loadtest_user() -> None:
    """Create the load-test user and show the credentials Locust needs."""
    from core.models import Membership
    from core.services.tenancy import ensure_default_organization
    from django.contrib.auth import get_user_model
    from rest_framework.authtoken.models import Token

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
    print(f"{verb} load-test user {USERNAME!r}.")
    print(f"  username: {USERNAME}")
    print(f"  password: {PASSWORD}")
    print("Use with LOCUST_USERNAME / LOCUST_PASSWORD; see docs/development/load-testing.md.")
