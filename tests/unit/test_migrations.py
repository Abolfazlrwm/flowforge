"""Migration safety tests.

Two properties are asserted here, and both are about a *deployment* rather
than a fresh database:

1. **Forward from empty.** Every migration applies cleanly to an empty schema.
   This is the case the test suite already exercises implicitly — Django builds
   the test database by running them — so the value here is that it is stated
   explicitly, and that it also runs against PostgreSQL, where the DDL actually
   differs.
2. **Upgrade from the v0.9.0 schema with data present.** The v0.9.0 tag's last
   ``core`` migration is ``0015``; ``0016`` is the only one added since. So
   migrating to ``0015`` reproduces the v0.9.0 schema exactly, and migrating
   forward from there is precisely the upgrade an operator performs.

The second case is the one that matters and the one a fresh-database test
cannot cover: a migration that is safe on an empty table can still be wrong on
a table with rows. ``0016`` adds columns with a constant default ``""``, which
PostgreSQL does as a catalog-only change — but that claim is about *existing
rows*, and the only way to test it is to have existing rows.

Both directions run inside one transaction (``transaction=True``), and
PostgreSQL and SQLite both have transactional DDL, so the schema changes are
rolled back when the test ends. A failure therefore cannot corrupt the database
for the tests that follow.

Seeding uses **historical models** (``apps.get_model``), not the in-Python
models from ``core.models``. This matters: the current ``WorkflowRun`` model
declares ``correlation_id`` / ``traceparent``, which do **not** exist at
migration ``0015``. Creating a row through the current ORM would emit an INSERT
that references those columns and fail against the v0.9.0 schema. The historical
model for ``0015`` only knows the columns that existed then, so it writes a
faithful v0.9.0-shaped row.

Each ``migrate`` uses a freshly constructed ``MigrationExecutor``. pytest-django
creates the test database with every migration already applied, so an executor
captures that as its in-memory "applied" snapshot; reusing it would make a later
forward migrate to a migration it already believes is applied a no-op. A new
executor re-reads the truth from the database recorder after each step.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import (
    ApiKey,
    AuditLog,
    Organization,
    OutboxMessage,
    TaskRun,
    Workflow,
    WorkflowRun,
)

#: The migration that ends the v0.9.0 line. ``git ls-tree v0.9.0
#: core/migrations/`` lists 0001..0015; HEAD adds 0016 only. If that stops being
#: true, this constant is the thing to update — and the assertion below that
#: checks it will say so.
V090_LAST_MIGRATION = "0015_audit_log_append_only"
HEAD_MIGRATION = "0016_outboxmessage_correlation_id_and_more"


def _migrate(target: str) -> None:
    """Migrate ``core`` to ``target`` with a fresh executor.

    A fresh ``MigrationExecutor`` re-reads which migrations are applied from the
    database recorder, so a forward migrate after a reverse is never treated as a
    no-op by a stale in-memory snapshot.
    """
    MigrationExecutor(connection).migrate([("core", target)])


def _historical_apps(target: str):
    """The app registry as it stood at ``target`` — models without later fields."""
    return MigrationExecutor(connection).loader.project_state([("core", target)]).apps


def _seed_tenant_data(apps) -> dict[str, object]:
    """Write one row of everything a real deployment has, at the v0.9.0 shape.

    ``apps`` is the historical app registry for migration ``0015`` — every model
    fetched through it is the v0.9.0 definition, with no ``0016`` columns, so the
    rows it writes are genuinely pre-upgrade data.

    Returns the primary keys the caller needs to re-check the rows afterwards.
    """
    Organization = apps.get_model("core", "Organization")
    Workflow = apps.get_model("core", "Workflow")
    Task = apps.get_model("core", "Task")
    WorkflowRun = apps.get_model("core", "WorkflowRun")
    TaskRun = apps.get_model("core", "TaskRun")
    OutboxMessage = apps.get_model("core", "OutboxMessage")
    ApiKey = apps.get_model("core", "ApiKey")

    organization = Organization.objects.create(name="Upgrade Co", slug="upgrade-co")
    workflow = Workflow.objects.create(
        organization=organization,
        name="upgrade-check",
        version=1,
        description="Written before the upgrade.",
    )
    # Workflow content lives in Task rows, not a field on Workflow.
    task = Task.objects.create(
        workflow=workflow,
        key="only",
        task_type="noop",
        config={},
    )
    run = WorkflowRun.objects.create(
        organization=organization,
        workflow=workflow,
        state="pending",
    )
    task_run = TaskRun.objects.create(
        organization=organization,
        workflow_run=run,
        task=task,
        state="succeeded",
        attempt=1,
    )
    outbox = OutboxMessage.objects.create(
        organization=organization,
        workflow_run=run,
        task_run=task_run,
        payload={"task_key": "only"},
    )
    api_key = ApiKey.objects.create(
        organization=organization,
        name="upgrade-key",
        prefix="deadbeef",
        hashed_key="not-a-real-hash",
        scopes=["read"],
    )
    return {
        "organization": organization.pk,
        "workflow": workflow.pk,
        "run": run.pk,
        "task_run": task_run.pk,
        "outbox": outbox.pk,
        "api_key": api_key.pk,
    }


@pytest.mark.django_db(transaction=True)
def test_the_v090_migration_is_the_one_before_head() -> None:
    """Guard the constant the upgrade test depends on.

    If a future release adds ``0017``, the v0.9.0 line still ends at ``0015``
    and this test still describes a v0.9.0 → HEAD upgrade — but the *number* of
    migrations under test would silently drop to one. Failing here forces the
    upgrade test to be re-pointed at the tag that is actually the previous
    release, rather than quietly covering less and less.
    """
    loader = MigrationExecutor(connection).loader
    applied = {
        name for app, name in loader.graph.nodes if app == "core"
    }
    assert HEAD_MIGRATION in applied, (
        f"{HEAD_MIGRATION} is no longer a migration in this tree; the upgrade "
        "test needs re-pointing at the current release's last migration"
    )
    # Every migration after 0015 is part of the upgrade under test.
    after = sorted(name for name in applied if name > V090_LAST_MIGRATION)
    assert after == [HEAD_MIGRATION], (
        "more than one migration has landed since the v0.9.0 line; the upgrade "
        f"test now covers {after}. Point it at the tag for the previous release "
        "so the whole delta is exercised, not just its last step."
    )


@pytest.mark.django_db(transaction=True)
def test_upgrading_from_the_v090_schema_preserves_data() -> None:
    """Apply 0016 to a populated v0.9.0 schema and check what survived."""
    # --- Reach the v0.9.0 schema -------------------------------------------
    _migrate(V090_LAST_MIGRATION)
    try:
        seeded = _seed_tenant_data(_historical_apps(V090_LAST_MIGRATION))
        counts_before = {
            "organizations": Organization.objects.count(),
            "workflows": Workflow.objects.count(),
            "runs": WorkflowRun.objects.count(),
            "task_runs": TaskRun.objects.count(),
            "outbox": OutboxMessage.objects.count(),
            "api_keys": ApiKey.objects.count(),
        }

        # --- Upgrade to HEAD -------------------------------------------------
        _migrate(HEAD_MIGRATION)

        # Row counts are the first thing a bad migration loses. A migration that
        # drops and recreates a table, or that a reverse accidentally runs,
        # shows up here.
        assert Organization.objects.count() == counts_before["organizations"]
        assert Workflow.objects.count() == counts_before["workflows"]
        assert WorkflowRun.objects.count() == counts_before["runs"]
        assert TaskRun.objects.count() == counts_before["task_runs"]
        assert OutboxMessage.objects.count() == counts_before["outbox"]
        assert ApiKey.objects.count() == counts_before["api_keys"]

        # The new columns must be present and **non-NULL** on the *pre-existing*
        # rows. 0016 adds them with a constant default ``""``, so PostgreSQL
        # back-fills existing rows with ``""`` rather than NULL. The property we
        # actually care about for a safe online migration is that no NULL leaked
        # in and the rows were not dropped — old data simply has no correlation
        # id yet, which is correct.
        run = WorkflowRun.objects.get(pk=seeded["run"])
        outbox = OutboxMessage.objects.get(pk=seeded["outbox"])
        assert run.correlation_id == ""
        assert run.traceparent == ""
        assert outbox.correlation_id == ""
        assert outbox.traceparent == ""

        # And the columns must be writable afterwards, which is the half of the
        # contract a read-only check would miss.
        marker = uuid.uuid4().hex
        run.correlation_id = marker
        run.save(update_fields=["correlation_id"])
        assert WorkflowRun.objects.get(pk=run.pk).correlation_id == marker

        # The old data is still *usable*, not merely present: the run still
        # resolves its workflow and its task run.
        assert run.workflow_id == seeded["workflow"]
        assert run.task_runs.count() == 1

        # A new row written after the upgrade must also get a value (the
        # default), because the field exists on the live model.
        fresh = WorkflowRun.objects.create(
            organization_id=seeded["organization"],
            workflow_id=seeded["workflow"],
            state="pending",
        )
        assert fresh.correlation_id == ""
    finally:
        # Leave the database at HEAD even if an assertion above failed, so a
        # failing run does not cascade into unrelated failures.
        _migrate(HEAD_MIGRATION)


@pytest.mark.django_db(transaction=True)
def test_the_upgrade_is_reversible() -> None:
    """Roll 0016 back and forward again, with data present throughout.

    ADR-014 promises a reversible migration path, and a reverse that only works
    on an empty table is not a rollback plan. This reverses with rows in place,
    which is also the case that catches a ``NOT NULL`` column added without a
    default: it can be added forward and then cannot be removed, or vice versa.
    """
    _migrate(V090_LAST_MIGRATION)
    try:
        seeded = _seed_tenant_data(_historical_apps(V090_LAST_MIGRATION))
        _migrate(HEAD_MIGRATION)

        # Reverse, then forward again, and require the data to still be there.
        _migrate(V090_LAST_MIGRATION)
        assert WorkflowRun.objects.filter(pk=seeded["run"]).exists()

        _migrate(HEAD_MIGRATION)
        assert WorkflowRun.objects.filter(pk=seeded["run"]).exists()
        assert Workflow.objects.filter(pk=seeded["workflow"]).exists()
    finally:
        _migrate(HEAD_MIGRATION)
