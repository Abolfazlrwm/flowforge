# Agent Instructions

This file tracks project context for automated agents and contributors.
**Read this file first.** If you are resuming cold, also read the "Resume point"
section of `docs/development/PROGRESS.md`.

## Current Phase

**Phase 4 — Scheduler: NEXT.** Phase 3 is complete (PR #23, CI run
`39454512221`); Phases 0–2 are merged, tagged and released.

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

## Phase 1 outcome

- PR #20 merged as `869720f`; `v0.2.0` tagged (annotated) and released.
- CI run `39226452632`: lint, test (143 unit + 5 PostgreSQL concurrency), docker
  all green; Codecov upload succeeded (token added 2026-10-19).
- Coverage: 97.55% (gate 80%).
- **Codecov reporting is now active.** The step had been failing silently since
  Phase 0 (tokenless upload rejected while reporting success); it now succeeds
  with `fail_ci_if_error: true`, and the README carries the badge.
- Known limitations: `docs/development/PROGRESS.md`.

## Phase 2 outcome

- PR merged with a merge commit; `v0.3.0` tagged (annotated) and released.
- **270 unit tests, 98.12% coverage** (gate 80%). 8 PostgreSQL tests: the 5
  transition races from Phase 1 plus 3 new publish races. All concurrency tests
  skip locally (SQLite) and run in CI with `REQUIRE_POSTGRES=1`.
- `docs/architecture/definitions.md` documents the definition lifecycle, the
  shape-vs-graph boundary, and what the publish race does not guarantee.
- **Four real defects were found and fixed during the phase**, each of them the
  same laziness class: a validation that existed but was never forced.
  - the parser accepted cyclic payloads because `graph` was a cached property
    nothing touched;
  - `load_definition` claimed graph validation and never forced it;
  - `ready_tasks` returned already-completed tasks, so a scheduler acting on it
    would run a task twice;
  - config JSON-shape was only checked at `__hash__`/`fingerprint()`, so a
    non-JSON value surfaced later as an unattributed `TypeError`.
  In every case the fix was to force the check at the boundary, and to add a
  regression test that is verified to **fail** without the fix.
- Plus one honest fix: `fingerprint()` omitted `description`, so publishing a
  definition whose only change was its description silently returned the stored
  row. Verified to fail without the fix before restoring it.
- **A local-environment defect was found rather than assumed**: `flowforge.settings.test`
  honoured a `DATABASE_URL` from `.env` because `base` merges the file into
  `os.environ`. A developer with a checked-out `.env` could not run the suite at
  all, contradicting the module's own "no external services" contract. The value
  is now snapshotted before `base` is imported; CI is unaffected.
- Known limitations: `docs/development/PROGRESS.md` (notably: the publish races
  are CI-verified only, and `publish_definition` does not serialise concurrent
  first-publishes).

## Phase 3 outcome

- **78 API tests**; 348 unit tests in total, 98.30% coverage, `api/` at 100%.
  The 8 PostgreSQL concurrency tests run in CI and all passed.
- `docs/api/openapi.yaml` is committed and a `schema` CI job fails on any drift.
  Generation was verified deterministic by generating twice and diffing.
- **Two defects were found by writing the tests, not by reading the code:**
  - `flowforge.settings.test` disabled throttling by emptying
    `DEFAULT_THROTTLE_RATES`, which left `ScopedRateThrottle` installed and
    turned every scoped request into a 500. It now removes
    `DEFAULT_THROTTLE_CLASSES`, and a test asserts every declared scope has a
    rate.
  - `IdempotencyMismatchError` was raised but never mapped, so the 422 the views
    and the schema promise was a 500. It is mapped now, and 422 was added to the
    status table.
  Both regression tests were reverted against the unfixed code to prove they
  fail without the fix.
- **One commit on this branch is broken in isolation.** `66484a0` added
  `api/views.py`, which unpacks `(workflow, created)` from
  `publish_definition`, but the matching change to `core/repositories/definitions.py`
  landed in `082d4ab`. The branch tip is consistent; the intermediate commit is
  not, so a bisect across it will fail to import. Recorded rather than hidden.
- Known limitations: `docs/api/overview.md` (no scheduler; throttle counters are
  per-process unless the cache is shared; the schema is checked for drift, not
  for semantics) and `docs/development/PROGRESS.md`.

## Current Phase scope

**Phase 3: REST API — COMPLETE.**

Delivered in Phase 3:
- `api/views.py`: five views. Each picks a queryset, declares permissions and
  throttles, calls one service, serialises the result. No business logic.
- `api/serializers.py`: output rendered from the database, not from the
  in-memory definition. Definition input is still validated only by
  `core.domain.schema`; `WorkflowDefinitionDoc` is a documentation mirror kept in
  step by `test_doc_serializer_matches_the_schema`.
- `api/errors.py`: one envelope for every failure, with domain-to-HTTP mapping in
  one place and unknown exceptions deliberately not swallowed.
- `api/permissions.py`, `api/pagination.py`, `api/filters.py`: the declared
  rules for who may write, how a list is paged, and which query parameters exist.
- Auth: DRF token and JWT, both checked against `is_active`.
- Idempotent triggers backed by a unique index on `(user, key)`.
- `docs/api/openapi.yaml` committed; `schema` CI job fails on drift.

**Phase 4: Scheduler** — next. See `docs/development/ROADMAP.md`. The state
machines, transitions repository and fencing tokens already exist; Phase 4 is
the eligibility rule, the claim loop, and the outbox — plus the ADR-007 decision
on whether Celery stays.

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
