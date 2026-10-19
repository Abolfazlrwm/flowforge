# Progress

## Current Phase

Phase 0 — Engineering Foundation (closing)

## Status

| Item | State |
|------|-------|
| Branch | `chore/phase0-audit-fixes` |
| Pull request | #1 |
| CI | Green — https://github.com/Abolfazlrwm/flowforge/actions/runs/39210906976 |
| Docker | **Verified in CI** (build + `docker compose up` + health probes) |
| v0.1.0 tag | Created after CI went green |
| Phase 1 | Not started |

## Completed

1. Isolated Git repository inside `FlowForge/`.
2. Django 5.1 project, package `flowforge`.
3. PostgreSQL, Redis, RabbitMQ wiring via `django-environ`.
4. Settings package: `flowforge/settings/{base,dev,test,prod}.py`.
5. `SECRET_KEY` mandatory when `DEBUG` is off; prod rejects short keys and
   placeholders and requires non-empty `ALLOWED_HOSTS`.
6. `core` app with `/health/live/` and `/health/ready/`.
7. Readiness probes PostgreSQL (`SELECT 1`), Redis (cache round-trip) and
   RabbitMQ (broker TCP connect), returning 503 with a per-dependency payload.
8. Celery wired as interim infrastructure (ADR-007).
9. `api/v1/` URL placeholder.
10. Ruff (lint + format), mypy strict, pytest, pytest-cov, pre-commit.
11. `uv.lock` committed; the Docker image and CI both install from it.
12. CI: lock check, lint, format, mypy, migration check, unit tests against
    PostgreSQL + Redis, and a compose smoke test that brings up all six
    services and probes both health endpoints.
13. All GitHub Actions pinned to commit SHAs; Dependabot covers `uv`,
    `github-actions` and `docker`.
14. Docker Compose stack with healthchecks and `service_healthy` dependencies.
15. Multi-stage Dockerfile, non-root `flowforge` user, `.dockerignore`.
16. Documentation: README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG,
    LICENSE, architecture, setup, deployment, roadmap, decisions, naming
    analysis, audit report, ADR-007 (Celery), ADR-009 (naming).
17. MkDocs configuration.
18. Makefile with `install`, `lint`, `format`, `typecheck`, `test`, `check`,
    `migrate`, `run`, `demo`, `compose-up`, `compose-down`, `clean`.

## Verification

### In CI (authoritative)

Run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39210906976

| Job | Result |
|-----|--------|
| lint (ruff, ruff format, mypy) | PASS |
| test (13 tests, coverage gate 80%) | PASS |
| docker (build, compose up, health probes) | PASS |

Docker job evidence:

```
stack ready after 3 attempt(s)
{"status": "ready", "checks": {"postgres": "ok", "redis": "ok", "rabbitmq": "ok"}}
{"status": "alive", "service": "flowforge"}
```

### Locally (Windows, Python 3.13.14)

| Check | Result |
|-------|--------|
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS |
| `uv run mypy flowforge core api` | PASS (17 files) |
| `uv run pytest tests/unit --cov` | 13 passed, 100% |
| `manage.py makemigrations --check --dry-run` | No changes detected |
| `manage.py check --deploy` (prod, strong key) | 0 issues |
| `docker compose config` | Valid |
| `detect-secrets` over tracked files + full history | 0 real secrets |

## Known limitations

### Architectural

1. **Celery is interim** (ADR-007). The custom scheduler, transactional outbox,
   direct RabbitMQ delivery and lease/fencing mechanism do not exist yet; the
   keep-or-replace decision belongs to Phase 4.
2. **No domain models, migrations or API endpoints.** `api/v1/urls.py` is an
   empty placeholder.
3. **Readiness checks are shallow.** RabbitMQ is checked with a TCP connect,
   not an authenticated AMQP handshake. Redis is checked with a cache write,
   which would also pass against a different cache backend.
4. **`make demo` does not run a workflow yet.** It brings up the stack and
   probes both health endpoints. A real end-to-end workflow demo becomes
   possible once Phase 5 (workers) lands.

### Tooling

5. **Base image pinned by tag, not digest.** `python:3.13-slim` can drift.
6. **No integration or end-to-end test suite** beyond the compose smoke test.
   `tests/integration/` and `tests/e2e/` are empty.
7. **Coverage is 100% of a very small codebase.** The number is not yet
   meaningful as a quality signal.
8. **Pre-commit hooks use `language: system`**, so they depend on the local
   environment rather than a locked one.
9. **Compose bind-mounts the source over `/app`**, which hides the image's
   `staticfiles/` output. Harmless with `DEBUG=True` (Django serves static
   itself) but wrong for a production-like run.

### Naming

10. **The name "FlowForge" is conflicted** — npm name reserved, PyPI
    `flow-forge` taken, a prior venture-funded company (now FlowFuse), and an
    active `flowforgesoft.com`. Five alternatives were measured and all are
    taken on PyPI (ADR-009). Retained by owner-delegated decision.

## Blockers

None. Docker is verified in CI; no local daemon is required.

## Next step

Merge PR #1, tag `v0.1.0`, then begin Phase 1 (Domain Modeling).
