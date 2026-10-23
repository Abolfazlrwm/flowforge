"""Make the audit log append-only at the database level.

Application code already only inserts, but "only inserts" is a convention, and a
convention is not a guarantee: a future view, a manual SQL session or a
`QuerySet.update()` could rewrite history. The trigger below turns that into a
database error, so the guarantee holds for every writer, not just the ones that
go through this codebase.

The SQL is vendor-specific (PostgreSQL and SQLite are the two the project runs
on). On PostgreSQL the row-level trigger is not fired by ``TRUNCATE``, which is
what lets the test runner flush the database between ``transaction=True`` tests;
on SQLite a flush deletes row by row, but the table is empty at that point, so
the trigger never fires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import migrations

if TYPE_CHECKING:
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
    from django.db.migrations.state import StateApps

_POSTGRES_INSTALL = (
    """
    CREATE OR REPLACE FUNCTION core_auditlog_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'core_auditlog is append-only';
    END;
    $$ LANGUAGE plpgsql;
    """,
    "DROP TRIGGER IF EXISTS core_auditlog_no_update ON core_auditlog;",
    """
    CREATE TRIGGER core_auditlog_no_update
    BEFORE UPDATE OR DELETE ON core_auditlog
    FOR EACH ROW EXECUTE FUNCTION core_auditlog_append_only();
    """,
)

_POSTGRES_REMOVE = (
    "DROP TRIGGER IF EXISTS core_auditlog_no_update ON core_auditlog;",
    "DROP FUNCTION IF EXISTS core_auditlog_append_only();",
)

_SQLITE_INSTALL = (
    """
    CREATE TRIGGER IF NOT EXISTS core_auditlog_no_update
    BEFORE UPDATE ON core_auditlog
    BEGIN
        SELECT RAISE(ABORT, 'core_auditlog is append-only');
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS core_auditlog_no_delete
    BEFORE DELETE ON core_auditlog
    BEGIN
        SELECT RAISE(ABORT, 'core_auditlog is append-only');
    END;
    """,
)

_SQLITE_REMOVE = (
    "DROP TRIGGER IF EXISTS core_auditlog_no_update;",
    "DROP TRIGGER IF EXISTS core_auditlog_no_delete;",
)


def install(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Create the append-only trigger for the current database vendor."""
    vendor = schema_editor.connection.vendor
    statements = {
        "postgresql": _POSTGRES_INSTALL,
        "sqlite": _SQLITE_INSTALL,
    }.get(vendor, ())
    for statement in statements:
        schema_editor.execute(statement)


def remove(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Drop the trigger (and, on PostgreSQL, its function)."""
    vendor = schema_editor.connection.vendor
    statements = {
        "postgresql": _POSTGRES_REMOVE,
        "sqlite": _SQLITE_REMOVE,
    }.get(vendor, ())
    for statement in statements:
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    """Enforce append-only audit rows with a database trigger."""

    dependencies = [
        ("core", "0014_auditlog"),
    ]

    operations = [
        migrations.RunPython(install, reverse_code=remove),
    ]
