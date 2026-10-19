# Progress

## Current Phase

Phase 0 — Engineering Foundation (audit closeout in progress)

## Status

| Item | State |
|------|-------|
| Branch | `chore/phase0-audit-fixes` |
| Pull request | #1 — **open, not merged** (awaiting owner approval) |
| v0.1.0 tag | **Not created** — blocked on Docker verification |
| Phase 1 | **Not started** |

## Completed

1. Isolated Git repository inside `FlowForge/` (verified root, no parent repo).
2. Django 5.1 project, package `flowforge`.
3. PostgreSQL, Redis, RabbitMQ wiring via `django-environ`.
4. Settings split into `flowforge/settings/{base,dev,test,prod}.py`.
5. `SECRET_KEY` is mandatory whenever `DEBUG` is off; `prod.py` additionally
   rejects short keys and known placeholder prefixes.
6. `core` app with `/health/live/` (liveness) and `/health/ready/` (readiness).
7. Readiness probes PostgreSQL (`SELECT 1`), Redis (cache set/delete) and
   RabbitMQ (TCP connect to broker host/port).
8. Celery wired as interim infrastructure (see ADR-007).
9. `api/v1/` URL placeholder.
10. Ruff (lint + format), mypy strict, pytest, pytest-cov, pre-commit.
11. `uv.lock` committed (82 packages); CI installs with `uv sync --locked`.
12. GitHub Actions CI: lock check, lint, format, mypy, migration check,
    tests with PostgreSQL + Redis services, Docker build.
13. All GitHub Actions pinned to commit SHAs; Dependabot configured for `uv`,
    `github-actions` and `docker`.
14. Docker Compose stack (db, redis, rabbitmq, web, scheduler, worker) with
    healthchecks and `service_healthy` dependencies.
15. Multi-stage Dockerfile with non-root `flowforge` user and `.dockerignore`.
16. Documentation: README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG,
    LICENSE (Apache-2.0), architecture overview, setup, deployment, roadmap,
    decisions, naming analysis, audit report.
17. MkDocs configuration (`mkdocs.yml`).
18. ADR-007 documents the Celery deviation and defers the decision to Phase 4.

## Verification results (2026-10-19, local)

| Check | Command | Result |
|-------|---------|--------|
| Lint | `ruff check .` | PASS |
| Format | `ruff format --check .` | PASS (41 files) |
| Types | `mypy flowforge core api` | PASS (17 files) |
| Tests | `pytest tests/unit` | PASS (13 tests) |
| Coverage | `pytest --cov` | above the 80% gate |
| Migrations | `makemigrations --check --dry-run` | No changes detected |
| Compose syntax | `docker compose config` | Valid |
| Liveness | `GET /health/live/` on a real server | 200 `{"status":"alive"}` |
| Readiness (deps down) | `GET /health/ready/` on a real server | 503 `{"status":"degraded"}` |
| Deploy check | `manage.py check --deploy` (prod settings, strong key) | 0 issues |
| Secrets | `detect-secrets` over 45 tracked files + all 3 commits | 0 real secrets (10 dev placeholders) |

## Known limitations

### Unverified

1. **Docker build and `docker compose up` are UNVERIFIED.** The Docker daemon
   was offline on the audit machine (`docker info` → cannot connect to
   `dockerDesktopLinuxEngine`). Only `docker compose config` (syntax) passed.
   No image has been built or run.
2. **CI has never executed on GitHub.** The workflow is defined and pinned but
   has not produced a run yet.
3. **v0.1.0 is not tagged.** It is gated on the owner confirming Docker works
   on their machine.

### Architectural

4. **Celery is interim** (ADR-007). The specification calls for a custom
   scheduler, a transactional outbox, direct RabbitMQ delivery, and
   lease-based execution with fencing tokens. None of those exist yet; the
   decision to keep or replace Celery is deferred to Phase 4.
5. **No domain models, migrations, or API endpoints.** `api/v1/urls.py` is an
   empty placeholder; `makemigrations` reports no changes because there are no
   models.
6. **Readiness checks are shallow.** RabbitMQ is verified by a TCP connect, not
   an authenticated AMQP handshake. Redis is verified by a cache write, which
   would also pass against a non-Redis cache backend if the setting changed.

### Tooling

7. **Docker does not consume `uv.lock`.** The image installs with `pip install .`;
   only CI resolves from the lock file. Image builds are therefore not
   bit-for-bit reproducible.
8. **Base image is pinned by tag, not digest.** `python:3.13-slim` can drift
   between builds.
9. **No integration or end-to-end tests.** `tests/integration/` and `tests/e2e/`
   exist but are empty. The 80% coverage gate is met by a small unit suite, so
   the number is not yet meaningful.
10. **Pre-commit hooks use `language: system`** and depend on whatever is on
    `PATH` rather than a locked environment.

### Naming

11. **The name "FlowForge" is heavily conflicted** — see
    [NAMING_ANALYSIS.md](NAMING_ANALYSIS.md). Highlights: the npm name is
    reserved by another account (publishing blocked), `flow-forge` is taken on
    PyPI, a prior venture-funded company used the name and is now FlowFuse,
    and `flowforgesoft.com` is an active commercial product. The owner chose to
    keep the name; no rename has been performed.

## Current blockers

- Docker daemon unavailable → cannot verify image build or container startup.
- Owner confirmation required before merging PR #1 or tagging v0.1.0.

## Next recommended task

After the owner verifies Docker: merge PR #1, tag v0.1.0, then begin Phase 1
(Domain Modeling). Phase 1 has not been started.
