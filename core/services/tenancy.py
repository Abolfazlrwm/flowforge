"""Organization helpers shared by the CLI commands and the test fixtures.

The data migration ``core.0012`` creates a default organization so that
pre-tenancy rows have an owner. Commands that run outside a request (the compose
smoke tests, the operator CLI) need the same organization, so the lookup lives
here rather than being re-implemented in each command.
"""

from __future__ import annotations

from core.models import Organization

#: Slug of the organization the backfill migration creates and the commands use.
DEFAULT_ORG_SLUG = "default"
DEFAULT_ORG_NAME = "Default Organization"


def ensure_default_organization() -> Organization:
    """Return the default organization, creating it if it is missing.

    Idempotent, so a command may call it unconditionally. In a migrated database
    the row already exists and this is a single ``SELECT``.
    """
    organization, _ = Organization.objects.get_or_create(
        slug=DEFAULT_ORG_SLUG,
        defaults={"name": DEFAULT_ORG_NAME},
    )
    return organization
