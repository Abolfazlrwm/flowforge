# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Django 5.1 → 5.2 LTS** (`django>=5.2,<5.3`). Django 5.1 reached end of
  extended support on 2025-12-03 and received no security fixes. Owner decision,
  recorded in [ADR-010](docs/adr/ADR-010-django-5-2-lts.md).
- **Django REST Framework 3.15.2 → 3.17.2.** 3.17 is the first series declaring
  support for Django 5.2; 3.15 does not.
- GitHub Actions upgraded and re-pinned to commit SHAs: `actions/checkout`
  v4 → v7.0.1, `actions/setup-python` v5 → v7.0.0,
  `astral-sh/setup-uv` v6 → v10.3.0, `codecov/codecov-action` v4 → v7.1.1.
- Dependabot now groups all GitHub Actions bumps into one PR and patch-level
  Python bumps into one PR, so a regression can be attributed to a single change.

### Fixed

- **Codecov upload had been failing silently since Phase 0.** The action
  rejected tokenless uploads (`Token required - not valid tokenless upload`)
  while reporting success, so the job looked green with no coverage data
  reaching Codecov. The step now skips when no `CODECOV_TOKEN` is configured and
  fails loudly when one is.

### Not upgraded, deliberately

- **Python stays at 3.13.** Django 5.2 supports 3.14 only from 5.2.8, and "every
  dependency verified on 3.14 in CI" has not been established. Docker image
  bumps for Python are ignored until it is.
- **redis-py stays at 5.x.** Celery 5.4 declares `redis<6.0.0` for its redis
  integration, and Celery is interim (ADR-007).
- **Celery stays at 5.4.0.** Interim infrastructure; the keep-or-replace
  decision belongs to Phase 4.

## [0.1.0] - 2026-10-19

First tagged release. Phase 0 (engineering foundation) is complete: the stack
builds, starts and reports healthy in CI.

Verified by
[CI run 39211983697](https://github.com/Abolfazlrwm/flowforge/actions/runs/39211983697)
— `lint`, `test` and `docker` all pass.

### Added

- Initial repository structure and Django 5.1 project (`flowforge` package).
- PostgreSQL, Redis, and RabbitMQ integration via `django-environ`.
- Environment-specific settings package: `flowforge/settings/{base,dev,test,prod}.py`.
- Celery configuration for distributed task processing (interim — see ADR-007).
- Liveness (`/health/live/`) and readiness (`/health/ready/`) endpoints.
- Readiness checks PostgreSQL (`SELECT 1`), Redis (cache round-trip) and
  RabbitMQ (broker TCP connect).
- `uv.lock` with a resolved, pinned dependency graph.
- Ruff, mypy (strict), pytest, pytest-cov, and pre-commit configuration.
- GitHub Actions CI: lock-file check, lint, format, mypy, migration check,
  unit tests against PostgreSQL + Redis services, a Docker build job, and a
  compose smoke test that brings up all six services and probes both health
  endpoints.
- All GitHub Actions pinned to commit SHAs.
- Dependabot configuration for `uv`, GitHub Actions, and Docker.
- Docker Compose stack with healthchecks and `service_healthy` dependencies.
- A one-shot `migrate` service that the application services wait on, so
  migrations never run concurrently.
- Multi-stage Dockerfile running as a non-root `flowforge` user, with
  `.dockerignore`.
- `.gitattributes` forcing LF in the working tree across platforms.
- Behavioural tests for readiness failure modes and the security flags.
- A `Makefile` with verifiable targets, including `make demo`.
- MkDocs configuration.
- `docs/adr/` with ADR-007 (Celery as interim infrastructure) and ADR-009
  (project name evaluation).
- `docs/development/NAMING_ANALYSIS.md` recording the "FlowForge" name conflicts.
- Apache-2.0 license and initial documentation.

### Changed

- Health endpoint split into separate liveness and readiness probes.
- Settings split from a single module into per-environment modules.
- `SECRET_KEY` is now required whenever `DEBUG` is off; `prod.py` additionally
  rejects keys shorter than 50 characters and known placeholder prefixes.
- CI now installs from `uv.lock` via `uv sync --locked`.
- Docker image no longer installs development dependencies.
- `pyproject.toml` `source`/`packages` paths corrected to the real layout
  (`flowforge/`, `core/`, `api/`).
- Compose environment hoisted into a single YAML anchor.

### Fixed

- `ruff format --check` failures in `asgi.py`, `wsgi.py`, `manage.py`
  (single quotes → double quotes).
- Coverage `source` pointing at a non-existent `src/` directory, which made
  every run report 0% and fail the 80% gate.
- Missing `pytest-cov` in dev dependencies while `--cov` was used in CI.
- Missing `.ruff_cache/` in `.gitignore`.
- Missing `.dockerignore`.
- Dockerfile `HEALTHCHECK` pointing at the removed `/health/` endpoint.
- Missing production security settings (`SESSION_COOKIE_SECURE`,
  `CSRF_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, `CSRF_COOKIE_HTTPONLY`,
  `SECURE_REFERRER_POLICY`).
- Broken `[project.scripts]` entry point referencing a non-existent `cli.main`.
- Migration check and Docker build were absent from CI.
- `web`, `worker` and `scheduler` ran `migrate` concurrently and raced on
  `CREATE TABLE` (`ProgrammingError: relation "django_session" already
  exists`). Migrations now run once, in a dedicated service.
- `scheduler` and `worker` were missing `DJANGO_ALLOWED_HOSTS`.

### Removed

- "TaskForge" rename. An earlier draft renamed the project without owner
  approval; the rename has been fully reverted and the project remains
  "FlowForge".
