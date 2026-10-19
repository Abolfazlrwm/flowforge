# Agent Instructions

This file tracks project context for automated agents and contributors.
**Read this file first.** If you are resuming cold, also read the "Resume point"
section of `docs/development/PROGRESS.md`.

## Current Phase

**Phase 1 — Domain Modeling: IN PROGRESS.** Phase 0 is closed and released.

## Completed (Phase 0)

- Repository initialized with an isolated `.git`.
- Django project created (package: `flowforge`); now on **5.2 LTS** (ADR-010).
- PostgreSQL, Redis, RabbitMQ integration configured.
- Settings split into `flowforge/settings/{base,dev,test,prod}.py`.
- `SECRET_KEY` required whenever `DEBUG` is off.
- Celery task queue wired (interim — see ADR-007).
- Liveness (`/health/live/`) and readiness (`/health/ready/`) endpoints with
  PostgreSQL, Redis, and RabbitMQ checks.
- Ruff, mypy (strict), pytest, pytest-cov, pre-commit.
- `uv.lock` committed; both CI and the image install with `uv sync --locked`.
- CI: lock check, lint, format, mypy, migration check, tests, Docker build, and
  a compose smoke test that boots all services and probes both health endpoints.
  All actions pinned to commit SHAs.
- Compose stack with a one-shot `migrate` gate; multi-stage Dockerfile running
  as non-root `flowforge`.
- Dependabot (`uv`, GitHub Actions, Docker); secret scanning and push
  protection; branch protection on `main`; issue and PR templates; 36 labels;
  11 phase milestones.
- ADR-007 (Celery interim), ADR-009 (name evaluation), Apache-2.0, MkDocs.

## Phase 0 outcome

- Audit performed 2026-10-18, closed out 2026-10-19.
- PR #1 merged as `97c17bf`; `v0.1.0` tagged and released.
- Docker **verified in CI** (run `39212369063`), not on the owner's machine.
- **27 defects** found and fixed. See `docs/development/AUDIT_REPORT.md`.
- Known limitations: `docs/development/PROGRESS.md`.

### A lesson worth carrying forward

CI run `39210906976` was green, and the next run failed with
`ProgrammingError: relation "django_session" already exists` — three services
were racing on `migrate`. **A green run is evidence that one interleaving
worked, not that the system is correct.** When a check passes, ask what it did
not exercise.

## Current Phase scope

**Phase 1: Domain Modeling** — in progress. Unblocked: issue #11 was resolved by
moving to Django 5.2 LTS (ADR-010).

Planned tasks:
- Workflow entities (Workflow, Task)
- Execution entities (WorkflowRun, TaskRun)
- State machines and transition rules
- Domain services for workflow lifecycle
- Persistence models and migrations
- Comprehensive state transition tests

## Decisions

- Python 3.11+ (developing on 3.13).
- **Django 5.2 LTS** — owner decision on 2026-10-19, recorded in ADR-010.
  Django 5.1 reached end of extended support on 2025-12-03. Do not change the
  framework version unilaterally.
- **Python 3.13.** 3.14 is deferred until it is verified in CI; the Docker
  ecosystem ignores Python minor/major bumps for that reason.
- **redis-py stays below 6 and Celery stays at 5.4.0** until the Phase 4
  keep-or-replace decision. Celery 5.4 declares `redis<6.0.0`.
- Modular monolith with independent runtime processes.
- `django-environ` for 12-factor configuration.
- `uv` for dependency resolution; `uv.lock` is the source of truth for CI.
- Celery retained as interim infrastructure (ADR-007); keep-or-replace belongs
  to Phase 4.
- Project name remains **FlowForge** by owner decision on 2026-10-19.

## Constraints

- Do not commit `.env` files.
- Do not hardcode secrets.
- Do not create empty packages for aesthetics.
- Every feature must include tests.
- Conventional Commits required.
- **Do not squash-merge.** `AUDIT_REPORT.md` cites every fix by SHA.
- Do not merge PRs or create release tags without explicit owner approval, except
  when CI is fully green and the change is within the current phase.
- v1.0.0 requires explicit owner approval before it is tagged.
- Do not attribute decisions to the owner that they did not make. If a decision
  is needed, ask and wait.
- Never claim a check passed without the command output or the CI run URL.
  "Tests pass" is not evidence; the job name and result is.
