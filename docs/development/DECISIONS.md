# Architecture Decision Records

> Full ADR documents are in [`docs/adr/`](../adr/). This file provides a
> summary index.

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

## ADR-007: Celery as Interim Task Infrastructure (Phase 0)

**Context:** The spec calls for a custom scheduler with transactional outbox, direct RabbitMQ delivery, leases, and fencing tokens. Celery was added in Phase 0 as interim infrastructure.
**Decision:** Keep Celery for Phase 0/1. Evaluate replacement or augmentation in Phase 4.
**Rationale:** Celery provides immediate value for Docker Compose and CI testing. The transactional outbox and lease management are domain concerns that can be layered on any delivery mechanism.
**Full ADR:** [`docs/adr/ADR-007-celery-as-interim-task-infrastructure.md`](../adr/ADR-007-celery-as-interim-task-infrastructure.md)

## ADR-008: Rename FlowForge to TaskForge

**Context:** The name "FlowForge" conflicts with a commercial product (flowforgesoft.com) and multiple GitHub repositories.
**Decision:** Rename the project to "TaskForge".
**Rationale:** "TaskForge" is available on PyPI, npm, and GitHub. Avoids trademark confusion and enables future publication.
**Full ADR:** [`docs/adr/ADR-008-rename-flowforge-to-taskforge.md`](../adr/ADR-008-rename-flowforge-to-taskforge.md)
