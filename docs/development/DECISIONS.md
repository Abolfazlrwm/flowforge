# Architecture Decision Records

## ADR-001: Python 3.11+ and Django 5.1

**Context:** Need a stable, modern Python/Django baseline.
**Decision:** Target Python 3.11+; develop on 3.13. Use Django 5.1.
**Rationale:** Django 5.1 supports Python 3.10–3.13. Python 3.11+ provides significant performance improvements and typing features.

## ADR-002: Modular Monolith with Separate Runtime Processes

**Context:** Need to balance simplicity with distributed execution requirements.
**Decision:** Start with a modular monolith (single deployable) but run scheduler and workers as independent processes.
**Rationale:** Avoids premature microservice complexity while enabling horizontal scaling of workers. Can extract services later if needed.

## ADR-003: PostgreSQL as Authoritative State

**Context:** Workflow execution state must be strongly consistent.
**Decision:** Use PostgreSQL as the single source of truth for all execution state.
**Rationale:** ACID transactions, row-level locking, and advisory locks provide the primitives needed for safe concurrent state transitions.

## ADR-004: RabbitMQ for Task Dispatch

**Context:** Need reliable asynchronous task delivery to workers.
**Decision:** Use RabbitMQ as the Celery broker.
**Rationale:** Mature, reliable, supports persistence and acknowledgments. Planned for at-least-once delivery with idempotent consumers.

## ADR-005: django-environ for Configuration

**Context:** Need 12-factor app configuration.
**Decision:** Use `django-environ` to parse `DATABASE_URL`, `REDIS_URL`, etc.
**Rationale:** Single `.env` file, type coercion, well-maintained.

## ADR-006: Ruff + mypy for Code Quality

**Context:** Need fast, consistent linting and type checking.
**Decision:** Use Ruff (replaces flake8, black, isort) and mypy in strict mode.
**Rationale:** Ruff is extremely fast and unified. mypy strict mode catches errors early.
