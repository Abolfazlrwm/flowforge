# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-10-21

Phase 4 (Scheduler) is complete. ADR-011 replaces Celery with three explicit
processes: a scheduler that decides *which* tasks dispatch, a publisher that
guarantees messages are never lost, and a worker (Phase 5) that executes. The
transactional outbox and lease reaper are in place, and a triggered run now
advances on its own instead of sitting in its initial state.

### Added

- `core/repositories/scheduler.py`: `advance_workflow_run` promotes
  dependency-satisfied tasks, starts the run and writes one outbox row per
  dispatch generation in a single transaction; `enqueue_ready_tasks` is
  idempotent per generation; `schedule_once` reaps expired leases then advances
  every active run.
- `core/models.OutboxMessage` + migration `core.0004_outbox`: the transactional
  outbox. Unique `(task_run, fencing_token_snapshot)` enforces one row per live
  dispatch generation; the partial index `idx_outbox_pending` serves the
  publisher scan.
- `core/broker.py`: the broker-agnostic `Publisher` protocol, an in-memory
  `FakePublisher`, `publish_once` (drain with `FOR UPDATE SKIP LOCKED`), and the
  `build_publisher` factory.
- `core/broker_rabbit.py`: `RabbitPublisher` over pika with publisher confirms.
- `manage.py scheduler` and `manage.py publisher`: the two long-running Phase 4
  processes, each with `--interval` and `--once`.
- The lease reaper (`reclaim_task_run`, `reap_expired_leases`) and the
  `reap_lease` transition (RUNNING→READY), reachable only through the reaper.
- `RABBITMQ_URL` setting.

### Changed

- The scheduler never publishes directly: it writes an outbox row in the same
  transaction that made the task dispatchable, and the publisher is what talks to
  the broker.
- `compose.yaml` runs `manage.py scheduler` and `manage.py publisher`; the
  `worker` service is renamed `publisher`.
- The readiness probe's broker check reads `RABBITMQ_URL`.

### Removed

- Celery, kombu, billiard and vine, along with `flowforge/celery.py`, the
  `celery_app` import, the `CELERY_*` settings, and the Celery-based Dependabot
  ignore. ADR-007 is superseded by ADR-011.

### Tests

- 373 unit tests and 11 PostgreSQL concurrency tests (5 transition races, 2
  reaper races, 3 publish races, 1 publisher SKIP LOCKED race).

### Known limitation

- `RabbitPublisher` has not been exercised against a live broker in CI (no
  RabbitMQ in the pipeline). It is validated structurally; the
  delivery-confirmation path is the property the worker's `dispatch_id`
  de-duplication relies on.

## [0.4.0] - 2026-10-20

Phase 3 (REST API) is complete. The domain built in Phases 1 and 2 now has an
HTTP surface: authenticated, authorised, cursor-paginated, rate-limited, and
described by an OpenAPI document that is committed and checked for drift.

Verified by
[CI run 39456088066](https://github.com/Abolfazlrwm/flowforge/actions/runs/39456088066)
— `lint`, `test` (348 unit tests, 98.30% coverage, 8 PostgreSQL concurrency
tests), `schema`, `docs` and `docker` all pass, and the Codecov upload
(`Upload queued for processing complete`) was accepted.

### Added

- `api/views.py`: five views, each of which picks a queryset, declares
  permissions and throttles, calls one service and serialises the result. No
  business logic, so the rules stay testable without HTTP.
- `api/serializers.py`: output is rendered from the database rather than from
  the in-memory definition, so a client sees what was stored. Definition input
  is **not** validated here — the JSON Schema in `core.domain.schema` remains the
  single shape validator, and `WorkflowDefinitionDoc` is a documentation-only
  mirror that a test keeps in step with the real schema.
- `api/errors.py`: one envelope for every failure,
  `{"error": {"code", "message", "details"}}`, with domain-to-HTTP mapping in one
  place. A stable `code` distinguishes failures that share a status. Anything
  unrecognised returns `None` so Django's own 500 path runs instead of hiding a
  defect behind a tidy response.
- `api/permissions.py`: `IsAuthenticatedAndActive` checks `is_active` as well as
  authentication, because a deactivated account still holds a valid token.
  `IsStaffOrReadOnly` gates publishing; `IsOwnerOrStaff` is the object-level run
  rule, and it answers 403 rather than 404 because the row demonstrably exists.
- `api/pagination.py`: cursor pagination ordered by `(-created_at, -id)`.
  `created_at` alone is not unique, and a cursor landing inside a group of equal
  values duplicates a row. `page_size` is capped at 100.
- `api/filters.py`: the allowed query parameters as a declared set —
  `name`/`name__startswith`/`version` for definitions, and
  `state`/`workflow`/`created_after`/`created_before` for runs.
- Authentication: DRF token (revocable by deleting a row) and JWT (stateless,
  short-lived), plus `/auth/me/`. Both fail differently, so both are supported.
- Idempotent triggers: an `Idempotency-Key` replays the original run with
  `Idempotent-Replay: true`. The guarantee comes from a unique index on
  `(user, key)`, not a lock, because a row that does not exist cannot be locked.
- `docs/api/openapi.yaml`, committed, plus a `schema` CI job that regenerates it
  and fails on any diff. Generation was verified deterministic by generating
  twice and diffing.
- 78 API tests across auth, definitions, runs, permissions, the error envelope,
  the schema mirror and throttling.

### Changed

- `publish_definition` returns `(workflow, created)`. "201 vs 200" is now decided
  by the row that was actually written rather than by a pre-existence check that
  would race another publisher.
- `flowforge.settings.test` disables throttling by removing
  `DEFAULT_THROTTLE_CLASSES` rather than by emptying the rates.

### Fixed

- **The test settings turned every scoped request into a 500.** Emptying
  `DEFAULT_THROTTLE_RATES` left `ScopedRateThrottle` installed, and it raised
  `ImproperlyConfigured` for every view naming a `throttle_scope`. A regression
  test now asserts that every declared scope has a rate, and was confirmed to
  fail against the old settings.
- **`IdempotencyMismatchError` was raised but never mapped**, so the 422 the
  views document was a 500. It is now mapped, and 422 was added to the status
  table so a DRF 422 cannot be reported as `server_error`. Both the unit and the
  end-to-end test were reverted against the fix to prove they fail without it.

## [0.3.0] - 2026-10-19

Phase 2 (Workflow Definition Engine) is complete. A definition is now a
versioned value object that validates on the way in and on the way out, and
publishing it is atomic, idempotent by content, and exclusive by
`(name, version)` — so a run can be traced back to exactly the definition it
executed.

Verified by
[CI run 39252710339](https://github.com/Abolfazlrwm/flowforge/actions/runs/39252710339)
— `lint`, `test` (270 unit tests, 98.12% coverage, 8 PostgreSQL concurrency
tests) and `docker` all pass, and the Codecov upload
(`Upload queued for processing complete`) was accepted.

### Added

- `core/domain/definition.py`: `TaskDefinition` and `WorkflowDefinition` as
  frozen dataclasses with no framework import. Config is frozen into a
  `MappingProxyType` so a caller's dict cannot mutate a validated definition,
  and every config value is checked to be JSON-shaped at construction rather
  than at `JSONField` save time.
- `DependencyGraph` with duplicate-key, unknown-dependency and cycle detection,
  plus topological order, levels, readiness, and transitive closures. Cycle
  detection is iterative, so a 3000-deep chain whose back-edge sits at the far
  end is found rather than overflowing the interpreter stack.
- Determinism: `topological_order()` and `levels()` break ties by ascending task
  key, so the same definition yields the same schedule regardless of declaration
  order.
- `core/domain/schema.py`: a Draft 2020-12 JSON Schema for definition payloads
  reported with JSONPaths, kept separate from the graph checks it cannot
  express (`additionalProperties: false` so a typo'd field is not silently
  ignored; `uniqueItems` cannot catch a duplicate key across two objects, so the
  domain does).
- `WorkflowDefinition.fingerprint()`: a SHA-256 over name, version, description
  and every task's key, type, sorted dependencies and frozen config. Identical
  fingerprint means identical content, which is what makes republishing a no-op.
- `core/repositories/definitions.py`: `publish_definition`, `load_definition`
  and `definition_exists`. Publish forces the dependency graph before writing
  anything, writes the workflow and its tasks in one transaction, and refuses
  differing content under an existing `(name, version)`.
- Migration `core/0002_definition_payload.py` adding `description`,
  `task_type`, `depends_on` and `config`, so loading is lossless rather than
  reconstructing a task type from its key.
- `docs/architecture/definitions.md`: the definition lifecycle, why definitions
  are value objects, why cycle detection is iterative, the shape-vs-graph
  boundary, and an honest account of the publish race and what it does not
  guarantee.

### Changed

- **`WorkflowDefinition.fingerprint()` now includes `description`.** Previously
  a definition whose only change was its description returned the stored row
  unchanged and silently discarded the new text — the caller believed one thing
  and the database held another. Regression-guarded by
  `test_a_changed_description_changes_the_fingerprint`.
- **`TaskDefinition` validates config at construction.** `__hash__` and
  `fingerprint()` were the only callers of the freeze, so a non-JSON config
  value passed construction and surfaced later as an unattributed `TypeError`.
- **`flowforge.settings.test` ignores a `DATABASE_URL` that exists only in
  `.env`.** `base` merges `.env` into `os.environ`, which made a file value
  indistinguishable from a caller-supplied one, so any developer with a checked-out
  `.env` could not run the suite — contradicting the module's own "no external
  services required" contract. The value is now snapshotted before `base` is
  imported. CI is unaffected: it sets `DATABASE_URL` at job level.
- `tests/integration/conftest.py` now holds the barrier, the per-thread
  connection cleanup and the no-stragglers check, shared by the transition race
  and the publish race.

### Fixed

- **`publish_definition` could not distinguish a cyclic definition until after it
  had written rows.** The graph is a cached property; it is now forced before the
  transaction opens, so a cyclic definition raises before anything is stored.
- **`load_definition` was lossy.** It reconstructed `task_type` from the task key
  and dropped dependencies, contradicting its own docstring and making the
  fingerprint meaningless. It now round-trips through the same columns the
  payload was parsed from.
- **`load_definition` claimed graph validation but never forced it**, so a
  manually broken `depends_on` loaded successfully.
- **`ready_tasks` returned already-completed tasks.** A scheduler acting on that
  would execute a task twice. Excluded from the result, and covered by a
  Hypothesis property test rather than a hand-written case.

## [0.2.0] - 2026-10-19

Phase 1 (Domain Modeling) is complete. The rules of the engine now live in
`core/domain` as plain Python with no framework import, and the database enforces
the same rules through six `CHECK` constraints. State transitions are single
conditional `UPDATE`s; a fencing token incremented on claim makes a resurrected
worker's write match zero rows.

Verified by
[CI run 39226452632](https://github.com/Abolfazlrwm/flowforge/actions/runs/39226452632)
— `lint`, `test` (143 unit tests + 5 PostgreSQL concurrency tests) and `docker`
all pass, and the Codecov upload (`Upload queued for processing complete`) was
accepted.

### Added

- Pure-Python domain layer in `core/domain/` with no Django imports, enforced by
  an AST scan and a subprocess import test that blocks `django` on
  `sys.meta_path`.
- `WorkflowRunState` (5 values) and `TaskRunState` (7 values) as `StrEnum`, with
  the `StrEnum` equality trap documented and tested as regression prevention.
- `StateMachine[T]` generic with validated `Transition` tables, rejecting
  duplicate edges, edges out of terminal states, dead ends and cross-enum states
  at import time.
- `WORKFLOW_RUN_MACHINE` (5 edges) and `TASK_RUN_MACHINE` (9 edges) in
  `core/domain/machines.py`. Cancellation is three distinct edges
  (`cancel_pending`, `cancel_ready`, `cancel_running`) so the audit log names the
  source state.
- Frozen dataclass entities (`Workflow`, `Task`, `WorkflowRun`, `TaskRun`) with
  `__post_init__` invariants: terminal/finish-time equivalence, running/lease
  equivalence, finish-after-start, positive attempt and non-negative fencing
  token.
- Domain services (`initial_task_run_state`, `derive_workflow_run_state`) with
  precedence-based derivation: cancelled > failed > all-succeeded > running >
  pending.
- Persistence models (`core/models.py`) with UUID PKs, six `CHECK` constraints,
  and named indexes justified by the query each serves (see
  `docs/architecture/data-model.md`).
- Atomic conditional-UPDATE transition repository
  (`core/repositories/transitions.py`) with fencing token increment on claim and
  stale-token rejection on finish. No `SELECT ... FOR UPDATE`, no sleep, no
  retry loop.
- Hypothesis stateful property tests (`RuleBasedStateMachine`) for state-machine
  reachability, terminality, and no-self-loops, plus derivation precedence
  properties.
- Concurrency tests on real PostgreSQL
  (`tests/integration/test_concurrent_transitions.py`): 16 threads with a
  `threading.Barrier`, proving exactly one claim wins, exactly one start wins,
  and a stale fencing token loses even under concurrency.
- Architecture documentation: `docs/architecture/data-model.md` (entity model,
  constraints, index justification, known costs) and
  `docs/architecture/execution-model.md` (state diagrams, transition protocol,
  fencing-token sequence, lease lifecycle).

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
