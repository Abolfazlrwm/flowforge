"""Backfill existing tenant rows into a default organization.

This is the data half of the multi-tenancy migration (ADR-014). ``0011`` added
the nullable ``organization`` columns; this migration gives every row that
predates tenancy an owner, so no live row is left unscoped.

The migration is deliberately **reversible**: rolling back nulls the column on
exactly the rows that were backfilled and then removes the default organization
it created, leaving the pre-tenancy shape intact. It is safe to run against an
empty database (the default organization is still created, so the backfill
target exists for any row written between the two migrations).

No row is ever deleted and no column is dropped, so a rollback cannot lose
tenant data — it only forgets which organization a row belonged to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import migrations

if TYPE_CHECKING:
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
    from django.db.migrations.state import StateApps

#: The slug of the organization every pre-tenancy row is assigned to. Chosen so
#: an operator can recognise it; it is not a reserved word anywhere else.
DEFAULT_ORG_SLUG = "default"
DEFAULT_ORG_NAME = "Default Organization"

#: Every model that gained an ``organization`` column in ``0011``. Listed here
#: once so the forward and reverse paths cannot drift.
TENANT_MODELS = (
    "Workflow",
    "WorkflowRun",
    "TaskRun",
    "TaskRunEvent",
    "ScheduledTrigger",
    "OutboxMessage",
    "IdempotencyKey",
)


def _default_organization(apps: StateApps) -> object:
    """Return the default organization, creating it if it does not exist."""
    Organization = apps.get_model("core", "Organization")
    organization, _ = Organization.objects.get_or_create(
        slug=DEFAULT_ORG_SLUG,
        defaults={"name": DEFAULT_ORG_NAME},
    )
    return organization


def backfill(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Assign every unscoped tenant row to the default organization."""
    organization = _default_organization(apps)
    for model_name in TENANT_MODELS:
        model = apps.get_model("core", model_name)
        model.objects.filter(organization__isnull=True).update(organization=organization)


def unbackfill(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Detach the default organization's rows, then remove the organization.

    The columns stay nullable after ``0011``, so this restores the pre-backfill
    shape exactly. Children are detached *before* the parent is deleted, so the
    ``PROTECT`` foreign keys on some of these models do not block the rollback.
    """
    Organization = apps.get_model("core", "Organization")
    organization = Organization.objects.filter(slug=DEFAULT_ORG_SLUG).first()
    if organization is None:
        return
    for model_name in TENANT_MODELS:
        model = apps.get_model("core", model_name)
        model.objects.filter(organization_id=organization.id).update(organization=None)
    organization.delete()


class Migration(migrations.Migration):
    """Create the default organization and backfill every tenant row."""

    dependencies = [
        ("core", "0011_membership_organization_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse_code=unbackfill),
    ]
