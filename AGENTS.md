# Agent Instructions

This file tracks project context for automated agents and contributors.

## Current Phase

**Phase 0: Engineering Foundation — audit closeout.** Phase 1 has NOT started.

## Completed

- Repository initialized with an isolated `.git` (root verified as the project
  directory, not a parent repository).
- Django 5.1 project created (package: `flowforge`).
- PostgreSQL, Redis, RabbitMQ integration configured.
- Settings split into `flowforge/settings/{base,dev,test,prod}.py`.
- `SECRET_KEY` required whenever `DEBUG` is off.
- Celery task queue wired (interim — see ADR-007).
- Liveness (`/health/live/`) and readiness (`/health/ready/`) endpoints with
  PostgreSQL, Redis, and RabbitMQ checks.
- Code quality tools configured: Ruff, mypy (strict), pytest, pytest-cov,
  pre-commit.
- `uv.lock` committed; CI installs with `uv sync --locked`.
- GitHub Actions CI: lock check, lint, format, mypy, migration check, tests,
  Docker build. All actions pinned to commit SHAs.
- Docker Compose local development stack with healthchecks; multi-stage
  Dockerfile running as a non-root `flowforge` user.
- Dependabot configured (`uv`, GitHub Actions, Docker).
- MkDocs configuration.
- `docs/adr/` with ADR-007 (Celery as interim infrastructure).
- Apache-2.0 license and initial documentation.

## Audit Status

- Independent audit performed 2026-10-18 and closed out 2026-10-19.
- Fixes live on branch `chore/phase0-audit-fixes`, PR #1 — **not merged**.
- **v0.1.0 is not tagged.** It is gated on the project owner verifying Docker
  on their own machine.
- Docker build and `docker compose up` remain **UNVERIFIED** — the daemon was
  offline on the audit machine.
- Known limitations are enumerated in `docs/development/PROGRESS.md`.

## Next Phase

**Phase 1: Domain Modeling** — not started. Do not begin until the owner
confirms Phase 0 is closed and v0.1.0 is tagged.

Planned tasks:
- Workflow entities (Workflow, Task)
- Execution entities (WorkflowRun, TaskRun)
- State machines and transition rules
- Domain services for workflow lifecycle
- Persistence models and migrations
- Comprehensive state transition tests

## Decisions

- Python 3.11+ (developing on 3.13).
- Django 5.1.
- Modular monolith with independent runtime processes.
- `django-environ` for 12-factor configuration.
- `uv` for dependency resolution; `uv.lock` is the source of truth for CI.
- Celery retained as interim infrastructure (ADR-007); the keep-or-replace
  decision belongs to Phase 4.
- Project name remains **FlowForge** by owner decision on 2026-10-19. The name
  is conflicted (see `docs/development/NAMING_ANALYSIS.md`); an earlier
  unapproved "TaskForge" rename has been reverted.

## Constraints

- Do not commit `.env` files.
- Do not hardcode secrets.
- Do not create empty packages for aesthetics.
- Every feature must include tests.
- Conventional Commits required.
- Do not merge PRs or create release tags without explicit owner approval.
- Do not attribute decisions to the owner that they did not make. If a decision
  is needed, ask and wait.
