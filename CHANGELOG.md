# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
