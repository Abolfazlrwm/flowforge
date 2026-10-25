# Backup and restore

This document is the operational runbook for backing up and restoring a
FlowForge deployment. It covers two stateful components:

- **PostgreSQL** — the system of record (organizations, workflows, runs, the
  transactional outbox, API keys, the append-only audit log).
- **RabbitMQ** — the task broker. Its *messages* are durable (quorum queues,
  fsynced through Raft), but the durable *topology* (queues, exchanges,
  bindings, users, vhost, permissions) is configuration, not data, and is what
  you back up.

A restore drill that exercises exactly this procedure runs on **every CI run**
in the `docker` job (`Backup and restore drill`). The drill dumps the live
database, wipes it, restores it, asserts row counts are byte-for-byte identical
before and after, re-imports the RabbitMQ definitions, and then runs a full
workflow end to end against the restored database. What CI proves and what it
does not is called out at the bottom of this page — read it before you trust a
backup you have never tested.

## PostgreSQL

### What to back up

The entire `flowforge` database. There is no split between "data" and "schema":
a `pg_dump` of the database captures both, including the append-only audit-log
trigger (`core_auditlog_no_update`) installed by migration `0015`. Restoring the
dump reproduces that trigger, so the audit log stays append-only after a restore.

### Logical dump (`pg_dump`)

A logical dump is the portable, version-tolerant default. It restores onto a
fresh cluster and is what the CI drill uses.

```bash
# Custom format: compressed, restorable, parallel-friendly.
pg_dump -U flowforge -Fc flowforge > flowforge-$(date +%F-%H%M).dump

# Verify the file is a real dump before you trust it. The custom format starts
# with the five-byte magic PGDMP (50 47 44 4d 50) — note the capitals.
head -c 5 flowforge-$(date +%F-%H%M).dump | xxd    # expect "PGDMP"
```

Restore onto an emptied (or new) database:

```bash
# Wipe-then-restore onto the same database name:
psql -U flowforge -d postgres -c "SELECT pg_terminate_backend(pid) \
  FROM pg_stat_activity WHERE datname='flowforge' AND pid <> pg_backend_pid();"
psql -U flowforge -d postgres -c "DROP DATABASE flowforge;"
psql -U flowforge -d postgres -c "CREATE DATABASE flowforge OWNER flowforge;"

pg_restore -U flowforge --dbname=flowforge --no-owner --clean --if-exists \
  flowforge-<timestamp>.dump
```

`--no-owner` avoids needing superuser to reassign objects; `--clean --if-exists`
makes the same command safe whether the target is empty or pre-populated.

### Point-in-time recovery (PITR)

A logical dump is a snapshot. For a *time window* — "restore to 14:32, just
before the bad deploy" — you need continuous archiving: WAL archiving plus
base backups.

```ini
# postgresql.conf
archive_mode = on
archive_command = 'test ! -f /var/lib/postgresql/wal_archive/%f && cp %p /var/lib/postgresql/wal_archive/%f'
wal_level = replica
```

```bash
# 1. Base backup (pg_basebackup).
pg_basebackup -U flowforge -D /var/lib/postgresql/basebackup -Ft -z -P

# 2. To recover: restore the base backup, drop a recovery.signal / standby.signal
#    with a target, and let PostgreSQL replay WAL up to the chosen point:
#      recovery_target_time = '2026-01-15 14:32:00+00'
```

PITR is operator-infrastructure (managed Postgres services such as
Cloud SQL / RDS / Azure DB provide it as a managed feature). FlowForge does not
configure it; the application only requires that *some* PostgreSQL is reachable.
Validate PITR against your provider's documented procedure on a clone before you
depend on it.

### Restore verification

Do not declare a restore successful because `pg_restore` exited 0. Verify:

1. The application reports `200` on `/health/ready/` (DB + cache + broker all up).
2. Row counts match the pre-restore baseline for every tenant table.
3. A workflow actually *runs* — trigger one and watch it reach `succeeded`.

The CI drill automates all three. On a real recovery, run the same three checks
by hand (or script them) before you let traffic back in.

## RabbitMQ

RabbitMQ messages live in quorum queues and survive a node restart or a single
node loss (see `docs/deployment/ha.md`). What does **not** survive a full broker
rebuild is the *topology*: the queues, exchanges, bindings, the `flowforge`
user, the vhost, and its permissions. Back those up as definitions.

### Export

```bash
# The broker's own CLI. This needs neither the management plugin nor curl, so it
# works against the image the chart and compose file ship (`rabbitmq:3.13-alpine`,
# which enables neither). `-` writes the JSON to stdout.
docker compose exec -T rabbitmq rabbitmqctl export_definitions - > rmq-defs.json

# On a broker where the management plugin IS enabled, the HTTP API is equivalent:
curl -s -u flowforge:flowforge http://rabbitmq:15672/api/definitions > rmq-defs.json
```

This captures users, vhosts, permissions, queues (including their `quorum`
type and arguments), exchanges, and bindings. It does **not** capture messages —
by design; you back up topology, not in-flight work. In-flight work is recovered
by the engine's outbox + lease reaper, not by a definitions file.

### Import

```bash
# Re-create the topology from the export. The import takes a real path: 3.13
# advertises `<file_path | "-">` in its usage text, but validates `-` as a
# filename and rejects it (stdin is only read when no argument is given).
docker compose cp rmq-defs.json rabbitmq:/tmp/rmq-defs.json
docker compose exec -T rabbitmq rabbitmqctl import_definitions /tmp/rmq-defs.json

# Or, with the management plugin enabled:
curl -s -u flowforge:flowforge -H 'Content-Type: application/json' \
  --data-binary @rmq-defs.json \
  http://rabbitmq:15672/api/definitions/%2F      # %2F is the default vhost "/"
```

Re-importing is idempotent for resources that already exist with identical
arguments. The CI drill feeds the export straight back into the same broker to
prove the file is valid and re-importable; it does not wipe the broker first
(see "What CI does and does not prove" below).

## What CI does and does not prove

**Proven every run (`docker` job → `Backup and restore drill`):**
- A `pg_dump` of the live database is a valid custom-format archive.
- Dropping and recreating the database, then `pg_restore`, returns **identical**
  row counts across all eleven tenant tables (organizations, memberships, API
  keys, audit log, workflows, tasks, workflow runs, task runs, outbox, scheduled
  triggers, task-run events).
- The append-only audit-log trigger survives the restore (it is part of the
  dump and is recreated by `pg_restore`).
- A workflow executes end to end — trigger → scheduler → outbox → publisher →
  broker → worker → DB — against the **restored** database, with the
  application stack having been running the whole time.
- The RabbitMQ definitions export is valid JSON, contains the expected quorum
  queue, and is re-importable into the live broker.

**Not proven here (operate, but validate on your own infrastructure):**
- **PITR / WAL archiving.** This requires continuous-archiving infrastructure
  CI does not have. Validate it against your Postgres provider on a clone.
- **A full RabbitMQ broker wipe and rebuild.** The CI drill re-imports into the
  *same* running broker (a no-op for existing resources). A genuine
  destroy-and-rebuild of the broker is operator-run; the export format is the
  same one the drill validates.
- **Backup of large / production-scale data volumes.** The CI database holds a
  handful of rows. The dump/restore *procedure* is proven; its runtime against
  terabytes is not measured here.
- **Cross-engine restore** (e.g. dump on 16, restore on 17). PostgreSQL
  guarantees dump/restore compatibility across supported major versions; this is
  not separately tested.
