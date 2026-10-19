# Agent Instructions

This file tracks project context for automated agents and contributors.
**Read this file first.** If you are resuming cold, also read the "Resume point"
section of `docs/development/PROGRESS.md`.

## Current Phase

**Phase 0 is CLOSED. Phase 1 is NOT started — it is blocked on issue #11.**

## Completed (Phase 0)

- Repository initialized with an isolated `.git`.
- Django 5.1 project created (package: `flowforge`).
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

## Next Phase

**Phase 1: Domain Modeling** — not started.

Blocked on **issue #11**: Django 5.1 is past end-of-life (extended support
ended 2025-12-03). Phase 1 writes the migrations that this decision affects, so
do not start until the owner chooses between staying on 5.1 and moving to 5.2
LTS.

Planned tasks:
- Workflow entities (Workflow, Task)
- Execution entities (WorkflowRun, TaskRun)
- State machines and transition rules
- Domain services for workflow lifecycle
- Persistence models and migrations
- Comprehensive state transition tests

## Decisions

- Python 3.11+ (developing on 3.13).
- Django 5.1 — **owner decision, currently under review in issue #11 because
  5.1 is out of support.** Do not change it unilaterally.
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
