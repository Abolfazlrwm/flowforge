"""Fixture credential provisioning for the e2e / load-test stacks.

Run as ``python -m tests.e2e.provision <e2e|loadtest>`` from inside the
compose stack. This lives under ``tests/`` on purpose: it mints a staff
account with a fixed password, which must never be part of the shipped
management surface (``manage.py e2e_token`` used to survive into
production images).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

USERNAME = "e2e"
# The password is a fixed, obviously-fake string for a fixture account that only
# exists in the throwaway e2e stack. The annotation says only the test id: bandit
# reads a trailing comment as more test names and warns about each word.
PASSWORD = "e2e-password-not-for-production"  # noqa: S105  # nosec B105
TOKEN_FILE = Path(tempfile.gettempdir()) / "e2e_token"


def _refuse_with_production_settings() -> None:
    """Belt and braces: never mint fixture credentials under prod settings."""
    from django.conf import settings

    settings_module = getattr(settings, "SETTINGS_MODULE", "")
    if "prod" in settings_module.split("."):
        raise SystemExit(
            "refusing to provision fixture credentials with production "
            f"settings ({settings_module})"
        )


def provision_e2e() -> None:
    """Create (idempotently) the e2e user and write its DRF token to a file."""
    _refuse_with_production_settings()

    from django.contrib.auth import get_user_model
    from rest_framework.authtoken.models import Token

    from core.models import Membership
    from core.services.tenancy import ensure_default_organization

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
    print(f"e2e token written to {TOKEN_FILE}")


def provision_loadtest() -> None:
    """Create the load-test user and print its credentials."""
    _refuse_with_production_settings()

    from tests.e2e.loadtest_user import provision_loadtest_user

    provision_loadtest_user()


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"e2e", "loadtest"}:
        print("usage: python -m tests.e2e.provision <e2e|loadtest>", file=sys.stderr)
        return 2
    import django

    django.setup()
    if argv[1] == "e2e":
        provision_e2e()
    else:
        provision_loadtest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
