# Progress

## Current Phase

Phase 0 — Engineering Foundation

## Completed Tasks

1. Local Git repository initialized inside `TaskForge/` (isolated from parent repo).
2. Django 5.1 project created with `taskforge` project name.
3. Dependencies installed: Django, DRF, psycopg, Celery, Redis, pydantic, structlog, whitenoise, gunicorn, django-environ, jsonschema.
4. Dev dependencies installed: pytest, pytest-django, pytest-xdist, pytest-cov, ruff, mypy, django-stubs, djangorestframework-stubs, pre-commit, coverage.
5. Settings configured with 12-factor env vars, PostgreSQL, Redis cache, Celery, structlog.
6. `core` app created with `/health/live/` and `/health/ready/` endpoints.
7. `api/v1/` placeholder created.
8. Celery app configured in `taskforge/celery.py`.
9. Unit test for liveness endpoint passing.
10. Ruff linting and formatting passing.
11. mypy type checking passing.
12. Docker Compose configured with db, redis, rabbitmq, web, scheduler, worker services.
13. Dockerfile multi-stage build with non-root user.
14. GitHub Actions CI configured for lint, test (with migration check), and Docker build.
15. pre-commit hooks configured.
16. Dependabot configured for pip, GitHub Actions, and Docker.
17. Documentation: README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, LICENSE.
18. MkDocs configuration added.
19. ADR directory established with ADR-007 (Celery interim) and ADR-008 (rename).
20. .dockerignore and .ruff_cache in .gitignore added.
21. Agent tracking: AGENTS.md, ROADMAP.md, PROGRESS.md, DECISIONS.md.

## Audit Fixes Applied (chore/phase0-audit-fixes)

- Renamed project from FlowForge to TaskForge (ADR-008).
- Fixed `ruff format --check` failures (single quotes → double quotes in asgi.py, wsgi.py, manage.py).
- Split health endpoint into liveness (`/health/live/`) and readiness (`/health/ready/`) with DB, Redis, and RabbitMQ checks.
- Added missing security settings: SECURE_REFERRER_POLICY, SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE, SESSION_COOKIE_HTTPONLY, CSRF_COOKIE_HTTPONLY (all gated on `not DEBUG`).
- Fixed coverage `source` path from `["src"]` to `["taskforge", "core", "api"]` (src/ didn't exist).
- Fixed hatch wheel `packages` from `["src/flowforge"]` to `["taskforge", "core", "api"]`.
- Added `pytest-cov` to dev dependencies (was missing but `--cov` used in CI and addopts).
- Added non-root Docker user (`taskforge`).
- Added `.dockerignore`.
- Added migration check to CI (`makemigrations --check --dry-run`).
- Added Docker build job to CI.
- Added Dependabot config.
- Fixed Dockerfile HEALTHCHECK URL from `/health/` to `/health/live/`.
- Added missing `.ruff_cache/` to `.gitignore`.
- Removed `-n auto` from pytest addopts (conflicts with coverage measurement on some setups).
- Created `mkdocs.yml`.
- Created ADR directory with ADR-007 (Celery interim) and ADR-008 (rename).

## Remaining Tasks

- Push `chore/phase0-audit-fixes` branch and open PR.
- Rename GitHub repository from `flowforge` to `taskforge` (requires user action).
- Update remote URL after repo rename.
- Verify Docker Compose starts successfully (blocked: Docker daemon offline).

## Current Blockers

- Docker daemon is not running on this machine; Compose stack UNVERIFIED.

## Known Limitations

- No domain models yet (Phase 1).
- No real API endpoints yet (Phase 3).
- No custom scheduler or worker logic yet (Phases 4–5). Celery is interim.

## Next Recommended Task

Begin Phase 1: Domain Modeling.
