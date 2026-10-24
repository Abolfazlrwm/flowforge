# Migration safety and zero-downtime upgrades

This document classifies every `core` migration and explains how to upgrade a
running FlowForge deployment without downtime. It is the operational companion to
`tests/unit/test_migrations.py`, which proves the upgrade path on every CI run.

## How the upgrade is verified in CI

`tests/unit/test_migrations.py` runs against **PostgreSQL** (the same engine as
production) and asserts two properties:

1. **Forward from empty.** Every migration applies cleanly to a fresh schema.
   The test database is built by running them, so a broken migration fails the
   build before it ever reaches a release.
2. **Upgrade from the `v0.9.0` schema with data present.** The `v0.9.0` line
   ends at migration `0015`; `0016` is the only migration added since. The test
   migrates to `0015`, writes one row of every tenant model at the `v0.9.0`
   shape, migrates forward to `0016`, and then asserts:
   - every row count is preserved (no table was dropped or recreated),
   - the new `correlation_id` / `traceparent` columns are present and **non-NULL**
     on the pre-existing rows (back-filled with the `""` default, not `NULL`),
   - the new columns are writable, and a row written *after* the upgrade also
     gets a value,
   - the reverse path (`0016 → 0015 → 0016`) preserves data throughout.

If you add a migration beyond `0016`, re-point the two constants at the top of
that test at the new "previous release" boundary so the whole delta is still
exercised — the guard test fails loudly if more than one migration now sits
between `v0.9.0` and `HEAD`.

## Classification of every migration

Categories:

- **Safe online** — additive DDL (new table, nullable `AddField`, constraint
  swap) or a constant-default `AddField`. On PostgreSQL 11+ these take only a
  brief `ACCESS EXCLUSIVE` lock and, for a defaulted column, **no table
  rewrite**. They can be applied while the old code is serving traffic.
- **Data backfill** — a `RunPython` that writes rows. It holds no long schema
  lock, but its runtime is proportional to the number of rows it touches, so run
  it off-peak on large datasets.
- **Vendor DDL** — raw, database-specific SQL (a trigger). Applied in
  milliseconds; review the SQL, because it is not portable and is not checked by
  Django's cross-backend safety net.

| Migration | Kind | Category | Notes |
|-----------|------|----------|-------|
| `0001_initial` | 4× `CreateModel` | Safe online | New tables only. |
| `0002_definition_payload` | 4× `AddField` | Safe online | Additive columns with defaults. |
| `0003_api_support` | `AddField` + `CreateModel` | Safe online | Additive. |
| `0004_outbox` | `CreateModel` | Safe online | New table. |
| `0005_…_state_valid_and_more` | `RemoveConstraint`, 3× `AddField`, `AlterField(state)`, 2× `AddConstraint` | Safe online | `state` stays a `CharField(max_length=20)`; only the check constraint is swapped. No type change, no rewrite. |
| `0006_…_state_valid_and_more` | `RemoveConstraint`, `AlterField(state)`, `AddConstraint` | Safe online | Adds the `skipped` choice; same column type. |
| `0007_task_join_task_when` | 2× `AddField` | Safe online | Additive (`join`, `when`). |
| `0008_scheduledtrigger` | `CreateModel` | Safe online | New table. |
| `0009_taskrunevent` | `CreateModel` | Safe online | New table. |
| `0010_task_compensate_for` | `AddField` | Safe online | Additive (`compensate_for`). |
| `0011_membership_organization_and_more` | 2× `CreateModel`, many `AddField` | Safe online | Adds the nullable `organization` FKs (ADR-014). Nullable `AddField` is cheap. |
| `0012_backfill_default_organization` | `RunPython(backfill)` | **Data backfill** | One-time: assigns every pre-tenancy row to a default organization. Bulk `UPDATE` per model; reversible (detaches rows, deletes the default org). |
| `0013_apikey` | `CreateModel` | Safe online | New table. |
| `0014_auditlog` | `CreateModel` | Safe online | New table. |
| `0015_audit_log_append_only` | `RunPython(install)` | **Vendor DDL** | Installs a row-level `BEFORE UPDATE OR DELETE` trigger on `core_auditlog`. PostgreSQL-only fires on row changes, **not** on `TRUNCATE`, which is why the test runner can flush. Reversible (`remove`). |
| `0016_outboxmessage_correlation_id_and_more` | 4× `AddField` (default `""`) | Safe online | Constant-default columns: catalog-only on PostgreSQL 11+, existing rows back-filled with `""`, no rewrite. |

**Bottom line:** no migration in the history performs a column-type change that
would force a full table rewrite or a long `ACCESS EXCLUSIVE` lock. The only
non-additive steps are a bounded data backfill (`0012`) and a trigger install
(`0015`), both reversible.

## Zero-downtime upgrade procedure

FlowForge's migrations are additive, so the standard rolling-upgrade order is
safe:

1. **Back up first.** Take a `pg_dump` (see `docs/deployment/backup-restore.md`)
   before touching production. A migration is reversible, but a backup is
   cheaper than discovering otherwise at 3 a.m.
2. **Deploy new code and run migrations together, or migrate first.** Because
   every migration here is backward-compatible with the old code (additive
   columns, new tables, a trigger that only restricts writes the app never
   makes), you may run `python manage.py migrate` either before or after rolling
   the new image out. Most operators `migrate` as a separate one-shot job (see
   the Kubernetes `migrate` Job) right before the new pods start.
3. **Run `0012` off-peak on large datasets.** It is a single bulk `UPDATE` per
   tenant model. For tables in the millions of rows, schedule it in a maintenance
   window or batch it; the current implementation issues one `UPDATE` per model
   and is fine into the low millions, after which row-by-row batching is
   advisable.
4. **`0015` and `0016` are instant** — a trigger install (milliseconds) and
   defaulted column adds (catalog-only on PostgreSQL 11+). No window required.
5. **Roll out the new application pods.** Old and new pods may run
   simultaneously during the roll; both are compatible with the migrated schema.

### Rolling back

Every migration is reversible. To roll back:

1. **Deploy the old code first.** Never leave new code running against a schema
   it does not understand.
2. `python manage.py migrate core <target>` to the migration you want to stop
   at. Reversing `0016` drops the new columns; reversing `0015` drops the
   append-only trigger; reversing `0012` detaches rows from the default
   organization and deletes it (no tenant data is lost, only the assignment).

Do **not** use `migrate --fake` unless you fully understand the resulting
divergence between the recorded state and the actual schema.

## What is NOT tested here

- **Data volume.** The CI upgrade test seeds a handful of rows. It proves the
  *schema* upgrade preserves data and keeps columns non-NULL; it does not
  exercise the runtime of `0012` against production-scale tables. Validate that
  separately on a restored copy of production data before a major version bump.
- **Cross-version skips.** The test exercises `v0.9.0 → HEAD` only. Jumping
  several releases at once is supported by Django's linear migration chain, but
  practice it on a staging clone first.
