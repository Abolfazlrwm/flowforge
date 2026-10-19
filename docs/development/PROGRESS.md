# Progress

## Current Phase

**Phase 1 — Domain Modeling: IN PROGRESS.** Phase 0 is closed and released.

## Resume point

If this session is resumed cold, read this section first.

| Question | Answer |
|----------|--------|
| Where is the work | `main` is the only long-lived branch. Phase 0 is merged and tagged. |
| What is released | `v0.1.0`, annotated tag on `97c17bf`, with a GitHub Release. |
| Framework | **Django 5.2 LTS** (owner decision, [ADR-010](../adr/ADR-010-django-5-2-lts.md)). Python stays at 3.13. |
| What is next | Phase 1 — Domain Modeling, target `v0.2.0`. |
| What blocks Phase 1 | **Nothing.** Issue #11 was closed by ADR-010. |
| What else is open | Nothing pending an owner decision. The Dependabot backlog was resolved as one coordinated stack upgrade; see "Dependency decisions". |

## Status

| Item | State |
|------|-------|
| Branch | `main` |
| Phase 0 PR | #1 — **MERGED** as `97c17bf` |
| CI | Green — https://github.com/Abolfazlrwm/flowforge/actions/runs/39212369063 |
| Docker | **Verified in CI** (build + `docker compose up` + health probes) |
| v0.1.0 tag | Created — annotated, points at `97c17bf` |
| GitHub Release | Published — https://github.com/Abolfazlrwm/flowforge/releases/tag/v0.1.0 |
| Branch protection | Active on `main`: `lint`, `test`, `docker` required, strict, no force push, 0 approving reviews |
| Django | **5.2 LTS** (ADR-010) |
| Python | 3.13 (3.14 deferred — see limitations) |
| Phase 1 | In progress |

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
24. Coordinated stack upgrade: Django 5.2 LTS and DRF 3.17.2, four GitHub
    Actions majors re-pinned to commit SHAs, Dependabot grouping, and ignore
    rules with reasons for the three declined upgrades (ADR-010).

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

### Dependency deferrals

12. **Python stays at 3.13.** Django 5.2 supports Python 3.14 only from 5.2.8.
    The compiled dependencies do ship `cp314` wheels (`psycopg-binary` 11,
    `pydantic-core` 31, `rpds-py` 29) and the rest are pure Python, but "every
    dependency verified on 3.14 in CI" has not been demonstrated: that needs a
    3.14 entry in the CI matrix *and* a matching Docker base image. Docker
    Python minor/major bumps are ignored in `dependabot.yml` until then.
13. **redis-py is pinned below 6 by Celery, not by preference.** Celery 5.4
    declares `redis<6.0.0` for its redis integration, and this project uses
    Celery's Redis result backend. redis-py 8.1.0 cannot be adopted until the
    Phase 4 keep-or-replace decision (ADR-007). Note that Celery 5.6 replaces
    the direct pin with `kombu[redis]`, so upgrading Celery would unblock it —
    the two deferrals are linked.
14. **Celery stays at 5.4.0.** Interim infrastructure (ADR-007). Minor and major
    upgrades are ignored in `dependabot.yml`; patch releases still flow.
15. **Codecov reporting is inactive.** The upload requires a `CODECOV_TOKEN`
    secret that does not exist. The step now skips rather than pretending to
    succeed, so no coverage data reaches Codecov and there is no badge. This
    does not weaken the quality gate: the 80% threshold is enforced by
    `pytest-cov` inside the test job.

## Dependency decisions

The Dependabot backlog was resolved as a single coordinated upgrade rather than
nine separate merges, so a regression can be attributed to one change.

| Package | Proposed | Outcome |
|---------|----------|---------|
| Django | 5.1.15 → 5.2.18 | **Taken.** Owner decision, ADR-010. 5.1 is out of support. |
| djangorestframework | 3.15.2 → 3.17.2 | **Taken.** 3.17 is the first series declaring Django 5.2 support. |
| actions/checkout | 4 → 7.0.1 | **Taken.** Verified by the `lint`/`test`/`docker` jobs. |
| actions/setup-python | 5 → 7.0.0 | **Taken.** Same. |
| astral-sh/setup-uv | 6 → 10.3.0 | **Taken.** Same. |
| codecov/codecov-action | 4 → 7.1.1 | **Taken, and a pre-existing defect fixed.** The upload had been failing with `Token required - not valid tokenless upload` since Phase 0 while the step reported success. It now skips visibly when no token exists, and fails loudly when one does. |
| redis | 5.3.1 → 8.1.0 | **Declined.** Blocked by Celery 5.4's `redis<6` (limitation 13). Ignore rule added. |
| celery | 5.4.0 → 5.6.3 | **Declined.** Interim; Phase 4 decides (limitation 14). Ignore rule added. |
| python (Docker) | 3.13-slim → 3.14-slim | **Declined.** Not verified in CI (limitation 12). Ignore rule added. |

## Blockers

None. Phase 1 is unblocked.

## Next step

Phase 1 (Domain Modeling) on `feat/phase1-domain-modeling`.
