# Development Setup

## Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/) (the project installs from `uv.lock`)
- Docker & Docker Compose (for PostgreSQL, Redis, RabbitMQ, and the full stack)

## Install

```bash
make install          # uv sync --locked --extra dev
cp .env.example .env  # optional; the test settings ignore .env for the database
```

## Run the stack

```bash
make compose-up       # build and start every service in the background
make demo             # start, then assert both health probes
```

The compose stack uses `flowforge.settings.dev` and the database at
`postgres://flowforge:flowforge@db:5432/flowforge`. Only the database is needed
for the PostgreSQL test run below; start it alone with:

```bash
make db-up            # docker compose up -d db, then wait for pg_isready
```

## Checks

```bash
make lint             # ruff check
make format           # ruff format
make typecheck        # mypy --ignore-missing-imports
make test             # unit suite on SQLite (fast, no services)
make test-pg          # unit suite on PostgreSQL via the compose db
```

### Why `make test-pg` matters

The unit suite defaults to SQLite so it runs anywhere, but SQLite and PostgreSQL
do not have the same semantics. SQLite ignores `FOR UPDATE`, so a query that
PostgreSQL rejects (for example `FOR UPDATE` over a nullable outer join) passes
locally; it returns rows in insertion order; and it fires `DELETE` triggers that
PostgreSQL's `TRUNCATE` does not. Each of those has already hidden a real defect
that only CI's PostgreSQL run caught.

`make test-pg` starts the compose database, points `DATABASE_URL` at it, and runs
the **full** unit suite with `REQUIRE_POSTGRES=1`. It is the run to trust when a
change touches locking, ordering, migrations, or a trigger. CI runs the same
suite against PostgreSQL in the `test` job; the SQLite run is a fast pre-check,
not the authority.

Integration tests (`tests/integration`) always require PostgreSQL and RabbitMQ:

```bash
REQUIRE_POSTGRES=1 REQUIRE_RABBITMQ=1 uv run pytest tests/integration -v
```

## Migrations

```bash
uv run python manage.py makemigrations --check --dry-run   # what CI runs
uv run python manage.py migrate
```

## Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```
