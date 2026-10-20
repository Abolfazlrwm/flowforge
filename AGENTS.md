# Agent Instructions

This file tracks project context for automated agents and contributors.
**Read this file first.** If you are resuming cold, also read the "Resume point"
section of `docs/development/PROGRESS.md`.

## Current Phase

**Phase 5 — Distributed Workers: NEXT.** Phase 4 is complete (v0.5.0); Phases 0–3
are merged, tagged and released.

## Completed (Phase 0)

- Repository initialized with an isolated `.git`.
- Django project created (package: `flowforge`); now on **5.2 LTS** (ADR-010).
- PostgreSQL, Redis, RabbitMQ integration configured.
- Settings split into `flowforge/settings/{base,dev,test,prod}.py`.
- `SECRET_KEY` required whenever `DEBUG` is off.
- Scheduler and publisher management commands (ADR-011); Celery removed in Phase 4.
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
- ADR-007 (Celery interim; superseded by ADR-011), ADR-009 (name evaluation),
  ADR-010 (Django 5.2), Apache-2.0, MkDocs.

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

## Phase 4 outcome

- ADR-011 accepted (owner decision 2026-10-20): Celery removed; three explicit
  processes — scheduler (which tasks dispatch), publisher (messages never lost),
  worker (who executes, Phase 5). The worker claims, not the scheduler.
- `core/repositories/scheduler.py`: `advance_workflow_run` promotes
  dependency-satisfied tasks, starts the run and writes one outbox row per
  dispatch generation in one transaction; `schedule_once` reaps expired leases
  then advances every active run.
- `core/models.OutboxMessage` + `core/repositories/outbox.py`: the transactional
  outbox. Unique `(task_run, fencing_token_snapshot)` is the "exactly one
  dispatch per live generation" guarantee. `create_dispatch` pre-checks existence
  so the common duplicate never attempts the INSERT (catching the UNIQUE
  violation alone would poison an open SQLite transaction), and wraps the create
  in a savepoint for the rare cross-process race.
- `core/repositories/transitions.py`: the lease reaper (`reclaim_task_run`,
  `reap_expired_leases`) — conditional `UPDATE ... WHERE state='running' AND
  lease_expires_at < now`, bumping the fencing token so a resurrected worker's
  write matches zero rows. The `reap_lease` edge (RUNNING→READY) is reachable only
  through the reaper, never the generic transition API.
- `core/broker.py` + `core/broker_rabbit.py` + `manage.py publisher`: the
  publisher drains the outbox with `FOR UPDATE SKIP LOCKED`, publishes with
  publisher confirms, and marks a row delivered only after the broker acks.
- Celery, kombu, billiard and vine removed; `manage.py scheduler` and
  `manage.py publisher` replace celery beat/worker in compose; `RABBITMQ_URL`
  replaces `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`.
- Tests: **373 unit tests** and **11 PostgreSQL concurrency tests** (5 transition
  races, 2 reaper races, 3 publish races, 1 publisher SKIP LOCKED race). The
  concurrency tests skip on SQLite and run in CI with `REQUIRE_POSTGRES=1`.
- One real defect was found while wiring the scheduler: `create_dispatch` caught
  the UNIQUE violation but the SQLite transaction was already aborted, so the
  next query raised `TransactionManagementError`. Fixed with an existence
  pre-check plus a savepoint, regression-guarded by the idempotency test.
- Honest limit: `RabbitPublisher` has not been exercised against a live broker in
  CI (no RabbitMQ there). It is validated structurally; the delivery-confirmation
  path is the property the worker's `dispatch_id` de-duplication relies on.

## Current Phase scope

**Phase 4: Scheduler — COMPLETE (v0.5.0).**

**Phase 5: Distributed Workers** — next. See `docs/development/ROADMAP.md`. The
worker consumes a dispatch message, claims the task run (the fencing token is in
the payload), executes it, and calls `finish_task_run` with that token. The claim
primitive, lease and fencing machinery already exist; Phase 5 is the consumer
loop, the handler registry, and result persistence.

## Decisions

- Python 3.11+ (developing on 3.13).
- **Django 5.2 LTS** — owner decision on 2026-10-19, recorded in ADR-010.
  Django 5.1 reached end of extended support on 2025-12-03. Do not change the
  framework version unilaterally.
- **Python 3.13.** 3.14 is deferred until it is verified in CI; the Docker
  ecosystem ignores Python minor/major bumps for that reason.
- **Celery removed in Phase 4** (ADR-011). The scheduler and publisher are Django
  management commands; the worker is Phase 5. redis-py stays below 6 for the Redis
  cache backend, and that pin is now a cache decision, not a Celery one.
- Modular monolith with independent runtime processes.
- `django-environ` for 12-factor configuration.
- `uv` for dependency resolution; `uv.lock` is the source of truth for CI.
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
