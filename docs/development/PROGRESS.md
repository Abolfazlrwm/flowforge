# Progress

## Current Phase

**Phase 0 — Engineering Foundation: CLOSED.** Phase 1 is not started.

## Resume point

If this session is resumed cold, read this section first.

| Question | Answer |
|----------|--------|
| Where is the work | `main` is the only long-lived branch. Phase 0 is merged and tagged. |
| What is released | `v0.1.0`, annotated tag on `97c17bf`, with a GitHub Release. |
| What is next | Phase 1 — Domain Modeling, target `v0.2.0`. |
| What blocks Phase 1 | **Issue #11** — Django 5.1 is past end-of-life. The domain models and their migrations should not be written against an unsupported framework, so this needs an owner decision first. |
| What else is open | Dependabot PRs (#2–#9, #12 at the time of writing). Deliberately unmerged; see "Pending decisions". The exact set grows as Dependabot runs — treat the table below as the current snapshot, not a fixed list. |

## Status

| Item | State |
|------|-------|
| Branch | `main` |
| Phase 0 PR | #1 — **MERGED** as `97c17bf` |
| CI | Green — https://github.com/Abolfazlrwm/flowforge/actions/runs/39212369063 |
| Docker | **Verified in CI** (build + `docker compose up` + health probes) |
| v0.1.0 tag | Created — annotated, points at `97c17bf` |
| GitHub Release | Published — https://github.com/Abolfazlrwm/flowforge/releases/tag/v0.1.0 |
| Branch protection | Active on `main`: `lint`, `test`, `docker` required, strict, no force push |
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
19. Migrations run in a single one-shot `migrate` service that the application
    services wait on, so three containers can no longer race on `CREATE TABLE`.
20. `.gitattributes` forces LF in the working tree, so a Windows checkout is
    byte-identical to what CI and the containers execute.
21. Repository configuration: description, ten topics, Discussions enabled,
    secret scanning and push protection on, Dependabot security updates on,
    branch auto-delete, auto-merge, and "update branch" allowed.
22. Branch protection on `main` — `lint`, `test`, `docker` required, branch must
    be up to date, force pushes and deletions blocked, conversations must be
    resolved. Approving reviews are set to 0 because GitHub forbids
    self-approval and this is a single-maintainer project.
23. Issue templates (defect, feature request) with blank issues disabled and
    security reports routed to a private advisory; a pull request template that
    asks for evidence rather than a claim; 36 labels; 11 phase milestones.

## Verification

### In CI (authoritative)

Run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39211983697

| Job | Result |
|-----|--------|
| lint (ruff, ruff format, mypy) | PASS |
| test (13 tests, coverage gate 80%) | PASS |
| docker (build, compose up, health probes) | PASS |

Docker job evidence:

```
migrate status=exited exit=0
stack ready after 2 attempt(s)
{"status": "ready", "checks": {"postgres": "ok", "redis": "ok", "rabbitmq": "ok"}}
{"status": "alive", "service": "flowforge"}
```

An earlier run of the same commit series,
[39211260120](https://github.com/Abolfazlrwm/flowforge/actions/runs/39211260120),
failed in the docker job with `ProgrammingError: relation "django_session"
already exists`. That was defect #25 (concurrent migrations), fixed in
`a0571bf`. It is recorded rather than discarded because the previous green run
[39210906976](https://github.com/Abolfazlrwm/flowforge/actions/runs/39210906976)
passed only because of container start ordering.

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
10. **The migration gate needs Docker Compose v2.20+** for
    `service_completed_successfully`. Older Compose silently ignores the
    condition and the race returns. The stack is not yet gated on the Compose
    version.

### Naming

11. **The name "FlowForge" is conflicted** — npm name reserved, PyPI
    `flow-forge` taken, a prior venture-funded company (now FlowFuse), and an
    active `flowforgesoft.com`. Five alternatives were measured and all are
    taken on PyPI (ADR-009). Retained by owner-delegated decision.

## Pending decisions

These are waiting on the owner. They are listed here so they cannot quietly
become assumptions.

| # | Question | Why it is not decided here |
|---|----------|----------------------------|
| [#11](https://github.com/Abolfazlrwm/flowforge/issues/11) | Django 5.1 is past end-of-life (extended support ended 2025-12-03). Move to 5.2 LTS? | "Django 5.1" was an explicit owner decision. Changing the declared stack is not a call to make unilaterally — and Phase 1 writes the migrations that this affects. |
| [#8](https://github.com/Abolfazlrwm/flowforge/pull/8) | Django 5.1.15 → 5.2.18 | Same decision as #11, expressed as a dependency bump. |
| [#3](https://github.com/Abolfazlrwm/flowforge/pull/3) | Python 3.13-slim → 3.14-slim | "Python 3.13" was an explicit owner decision. |
| [#7](https://github.com/Abolfazlrwm/flowforge/pull/7) | redis-py 5.3.1 → 8.1.0 | Three major versions. Needs a compatibility read, not a rubber stamp. |
| [#9](https://github.com/Abolfazlrwm/flowforge/pull/9) | Celery 5.4.0 → 5.6.3 | Celery is interim (ADR-007). Bumping it now, before the Phase 4 keep-or-replace decision, spends effort on a component that may be removed. |
| [#12](https://github.com/Abolfazlrwm/flowforge/pull/12) | djangorestframework 3.15.2 → 3.17.2 | Two minor versions; the API surface in Phase 3 is written against it, so the version should be settled before the endpoints are. |
| [#2](https://github.com/Abolfazlrwm/flowforge/pull/2), [#4](https://github.com/Abolfazlrwm/flowforge/pull/4), [#5](https://github.com/Abolfazlrwm/flowforge/pull/5), [#6](https://github.com/Abolfazlrwm/flowforge/pull/6) | GitHub Actions major bumps (checkout 4→7, setup-python 5→7, setup-uv 6→10, codecov 4→7) | Each needs its own CI run to prove it still works; batching them hides which one broke. Low risk, no urgency. |

## Blockers

Phase 1 is blocked on decision #11. Everything else is unblocked.

## Next step

Get a decision on #11, then start Phase 1 (Domain Modeling) on
`feat/phase1-domain-modeling`.
