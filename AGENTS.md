# Agent Instructions

This file tracks project context for automated agents and contributors.

## Current Phase

**Phase 0: Engineering Foundation (Audit Complete)**

## Completed

- Repository initialized with isolated `.git`.
- Django 5.1 project created (package: `taskforge`).
- PostgreSQL, Redis, RabbitMQ integration configured.
- Celery task queue wired (interim — see ADR-007).
- Liveness (`/health/live/`) and readiness (`/health/ready/`) endpoints
  with DB, Redis, and RabbitMQ checks.
- Code quality tools configured: Ruff, mypy, pytest, pytest-cov, pre-commit.
- GitHub Actions CI pipeline for lint/type-check/test/migration-check/Docker-build.
- Docker Compose local development stack with healthchecks and non-root Dockerfile.
- Dependabot configured (pip, GitHub Actions, Docker).
- MkDocs configuration.
- ADR directory with ADR-007 (Celery interim) and ADR-008 (rename).
- Apache-2.0 license and initial documentation.

## Audit Status

- Phase 0 audit completed on 2026-10-18.
- Fixes applied on branch `chore/phase0-audit-fixes`.
- Docker daemon was offline during audit; Compose stack is UNVERIFIED.
- v0.1.0 tag deferred until Docker verification passes.

## Next Phase

**Phase 1: Domain Modeling**

Planned tasks:
- Workflow entities (Workflow, Task)
- Execution entities (WorkflowRun, TaskRun)
- State machines and transition rules
- Domain services for workflow lifecycle
- Persistence models and migrations
- Comprehensive state transition tests

## Decisions

- Python 3.11+ (using 3.13 for development).
- Django 5.1 LTS track.
- Modular monolith with independent runtime processes.
- `django-environ` for 12-factor configuration.
- Celery retained as interim infrastructure (ADR-007); custom scheduler
  decision deferred to Phase 4.
- Project renamed from FlowForge to TaskForge (ADR-008).
- Docker daemon was offline during Phase 0 setup and audit; Compose file
  is ready but UNVERIFIED.

## Constraints

- Do not commit `.env` files.
- Do not hardcode secrets.
- Do not create empty packages for aesthetics.
- Every feature must include tests.
- Conventional Commits required.
