# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added

- Initial repository structure and Django project setup (TaskForge).
- PostgreSQL, Redis, and RabbitMQ integration via django-environ.
- Celery configuration for distributed task processing (interim — see ADR-007).
- Liveness (`/health/live/`) and readiness (`/health/ready/`) endpoints.
- Readiness endpoint checks PostgreSQL, Redis, and RabbitMQ connectivity.
- Ruff, mypy, pytest, pytest-cov, and pre-commit configuration.
- GitHub Actions CI pipeline for linting, testing (with migration check),
  and Docker build.
- Docker Compose local development stack with healthchecks.
- Dependabot configuration for pip, GitHub Actions, and Docker.
- MkDocs configuration.
- ADR directory with ADR-007 (Celery interim) and ADR-008 (rename).
- Apache-2.0 license and initial documentation.

### Changed

- Renamed project from "FlowForge" to "TaskForge" (ADR-008).
- Health endpoint split into liveness and readiness probes.
- Dockerfile now uses non-root user (`taskforge`).
- CI now runs migration check and Docker build in addition to lint and test.
- pyproject.toml `source` and `packages` paths corrected to match actual
  layout (`taskforge/`, `core/`, `api/`).

### Fixed

- `ruff format --check` failures in `asgi.py`, `wsgi.py`, `manage.py`
  (single quotes → double quotes).
- Missing `.ruff_cache/` in `.gitignore`.
- Missing `.dockerignore`.
- Missing `pytest-cov` in dev dependencies.
- Coverage `source` pointing to non-existent `src/` directory.
- Dockerfile `HEALTHCHECK` URL pointing to old `/health/` endpoint.
- Missing production security settings (`SESSION_COOKIE_SECURE`,
  `CSRF_COOKIE_SECURE`, `SECURE_REFERRER_POLICY`).
