# Architecture Decision Records

> Full ADR documents are in [`docs/adr/`](../adr/index.md). This file provides a
> summary index.

## ADR-001: Python 3.11+ and Django 5.2 LTS

**Context:** Need a stable, modern Python/Django baseline.
**Decision:** Target Python 3.11+; develop on 3.13. Use Django 5.2 LTS.
**Rationale:** Django 5.1 reached end of extended support on 2025-12-03, so it receives no security fixes. Django 5.2 LTS is supported until April 2028 and runs on Python 3.10–3.13 (3.14 from 5.2.8). Python 3.11+ provides significant performance improvements and typing features.

> Superseded the original Django 5.1 pin on 2026-10-19. See [ADR-010](../adr/ADR-010-django-5-2-lts.md).

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

**Status:** Accepted by the project owner on 2026-10-19.
**Context:** The spec calls for a custom scheduler with transactional outbox, direct RabbitMQ delivery, leases, and fencing tokens. Celery was added in Phase 0 as interim infrastructure.
**Decision:** Keep Celery for Phase 0/1. The keep-or-replace decision is deferred to Phase 4.
**Rationale:** Celery provides immediate value for Docker Compose and CI testing. The transactional outbox and lease management are domain concerns that can be layered on any delivery mechanism.
**Full ADR:** [`docs/adr/ADR-007-celery-as-interim-task-infrastructure.md`](../adr/ADR-007-celery-as-interim-task-infrastructure.md)

## Naming: "FlowForge" retained

**Status:** Decided by the project owner on 2026-10-19. No rename performed.

The name was evaluated and found to be conflicted on npm (reserved), PyPI
(`flow-forge` taken), GitHub (multiple projects over 100 stars), and in
commerce (a prior venture-funded company, now FlowFuse, plus an active
`flowforgesoft.com`). The owner decided to keep "FlowForge" for Phase 0.
Evidence and re-evaluation triggers are recorded in
[NAMING_ANALYSIS.md](NAMING_ANALYSIS.md). An earlier draft of this file
contained an "ADR-008: Rename to TaskForge" entry; that ADR was never approved
by the owner and has been removed.


## Docker verification: CI is sufficient

**Status:** Decided by the project owner on 2026-10-19.

The Phase 0 closeout carried a rule — "do not tag a release until Docker has been
verified on the owner's machine" — because the local Docker daemon was offline and
only CI had ever built the image and booted the compose stack. The owner has
withdrawn that rule: the CI `docker` job (multi-stage build, `docker compose up`,
and both health probes answering) is accepted as sufficient evidence.

**Consequence:** releases are tagged when CI is green, with no additional local
Docker check as a precondition. The `docker` job remains a required status check on
`main`, so the evidence is still produced on every pull request.

## Documentation site: strict MkDocs build required from Phase 3

**Status:** Decided by the project owner on 2026-10-19.

The documentation site was never built: `mkdocs` was not in the dependency set and
no CI job ran it, so the nav was unverified and the Mermaid diagrams in
`data-model.md`, `execution-model.md` and `definitions.md` could not render. That
is limitation 19 in [PROGRESS.md](PROGRESS.md).

**Decision:** add a CI job that installs `mkdocs-material` and
`pymdown-extensions`, and runs `mkdocs build --strict`. The build must be green on
every pull request from Phase 3 onward. Deploying to GitHub Pages is deferred to
Phase 10.

**Consequence:** a broken internal link, a missing nav target, or a documentation
file that MkDocs cannot parse now fails the build. Mermaid support requires
`pymdownx.superfences` with a `mermaid` custom fence, which the build checks for
only insofar as the extension loads — a diagram that is syntactically wrong in
Mermaid is still not caught, and that gap is recorded rather than implied away.
