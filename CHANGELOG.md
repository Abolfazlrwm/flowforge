# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.1] - 2026-10-23

Phase 8 follow-ups.

### Added

- **Membership management API.** `GET/POST /api/v1/members/` and
  `PATCH/DELETE /api/v1/members/<uuid>/`. Only owner/admin may call them; an
  admin cannot modify an owner or grant the owner role; the last owner cannot be
  removed or demoted (owner rows are locked while the count is taken). Every
  real change writes an audit entry (`member.added`, `member.role_changed`,
  `member.removed`) in the same transaction; a no-op role change writes nothing.
  Another tenant's membership is a 404.
- **`make test-pg`** runs the full unit suite against the compose PostgreSQL
  with `REQUIRE_POSTGRES=1`; `make db-up` starts the database alone.

### Changed

- `docs/development/PROGRESS.md` rewritten against the code and CI: current
  branches and tags, state-machine edge counts, dependency versions, and only
  the limitations that still apply (the worker reconnect, classic queue, sibling
  cancellation and Celery entries were resolved and removed).
- `docs/development/setup.md` documents the PostgreSQL test run and why SQLite
  is not the authority for locking, ordering or trigger semantics.

## [0.9.0] - 2026-10-23

Phase 8 — Security & Multi-Tenancy.

### Added

- **Organizations and memberships.** `Organization` and `Membership` with
  `owner > admin > member > viewer` roles. Every tenant model (`Workflow`,
  `WorkflowRun`, `TaskRun`, `TaskRunEvent`, `ScheduledTrigger`, `OutboxMessage`,
  `IdempotencyKey`, `ApiKey`, `AuditLog`) carries an `organization` FK, and
  `OrganizationScopedManager.for_organization` is the sanctioned read path
  (ADR-014).
- **API keys.** `ff_<hex-prefix>_<hex-secret>`, stored only as a
  `make_password` hash, with a clear prefix for lookup, scopes
  (`read < write < admin`), expiry, rotation, revocation, and a one-time
  plaintext response. `ApiKeyAuthentication` accepts `Authorization: Api-Key`
  or `X-API-Key` and fixes the active tenant to the key's.
- **Append-only audit log.** `AuditLog` plus a database trigger that rejects
  `UPDATE` and `DELETE`; entries for publish, trigger, requeue and API-key
  create/rotate/revoke. Metadata is redacted before it is written.
- **Secret redaction.** `core.security.redact_mapping` redacts by key name and
  by an explicit `secret: true` marker; applied to task `config` in API
  responses and to audit metadata.
- **Tenant-isolation suite.** Every cross-tenant id is a 404, not a 403; a
  role-permission matrix covers every role; API-key lifecycle (expired, revoked,
  wrong scope, rotated, orphaned) is tested.
- **STRIDE threat model** ([`docs/security/threat-model.md`](docs/security/threat-model.md))
  and **ADR-014** ([`docs/adr/ADR-014-multi-tenancy-shared-schema.md`](docs/adr/ADR-014-multi-tenancy-shared-schema.md)).
- **`bandit` and `pip-audit`** as required CI checks.

### Changed

- **Authorization is per organization, not per user.** A run is visible to every
  member of its tenant; another tenant receives a 404. Staff remain a narrow
  operational read bypass, and an API key never inherits it.
- Definitions are unique on `(organization, name, version)`; idempotency claims
  are unique on `(organization, user, key)`, so two tenants may reuse a name or a
  key without colliding and a replay cannot cross tenants.
- The dispatch payload carries `organization_id` for attributable worker logs.
- `djangorestframework` 3.17.2 → 3.18.3 (CVE-2026-114136/114137/114138) and
  `certifi` 2026.10.18 → 2026.10.19, forced by `pip-audit`.

### Migrations

- `core.0011` — schema: organizations, memberships, and the `organization` FK.
- `core.0012` — data: default-organization backfill, reversible.
- `core.0013` — API keys.
- `core.0014` — audit log.
- `core.0015` — the append-only trigger.

## [0.8.1] - 2026-10-22

Closes the two Phase 7 end-to-end gaps and makes the event log transactional.

### Added

- **End-to-end cron and saga checks in the compose CI job.** `cron_e2e` lets the
  running scheduler fire a `ScheduledTrigger` and requires the triggered run to
  reach `SUCCEEDED` through the real outbox → publisher → RabbitMQ → worker path;
  `saga_e2e` fails a task and requires its compensator to actually execute through
  a real worker, with the run settling at its final `FAILED` state. A `fail`
  built-in handler produces the deterministic failure.
- **ADR-013** ([`docs/adr/ADR-013-event-log-is-transactional.md`](docs/adr/ADR-013-event-log-is-transactional.md)):
  the `TaskRunEvent` audit entry is written in the same transaction as the state
  change it describes, on every path.

### Changed

- `finish_task_run` and `cancel_task_run` now wrap the state `UPDATE` and the event
  insert in one `transaction.atomic()` (ADR-013). A crash rolls back the state change
  and its event together, so the audit trail has no silent gap where the state moved
  without an entry. The other transition paths and the outbox were already
  transactional.

## [0.8.0] - 2026-10-22

Phase 7 — Advanced Workflows. Fan-out/fan-in, conditional branching on a sandboxed
expression language, cron scheduling with no double-fire across instances,
cooperative cancellation, an append-only event log, manual re-run of a failed task,
and saga-style compensation.

### Added

- **Conditional branching and fan-out/fan-in.** A task may declare a `when`
  condition over the run's task results and a `join` policy — `all` (every
  dependency must succeed, the default) or `any` (one surviving path is enough,
  for a fan-in over alternative branches). A false condition `SKIP`s the task, and
  a skip propagates to its dependants so a branch that was not taken cannot stall
  the run. New `TaskRunState.SKIPPED`.
- **A sandboxed expression evaluator** (`core/domain/expressions.py`). `when`
  conditions are evaluated by a hand-written tokenizer + recursive-descent parser
  and evaluator — no `eval`, no attribute access, no calls, no indexing — with
  injection tests. A broken condition is a loud definition error, never a silent
  skip.
- **Cron scheduling.** `ScheduledTrigger` starts a workflow run on a cron schedule.
  The scheduler fires each due trigger with `SELECT FOR UPDATE SKIP LOCKED`, so N
  scheduler instances cannot double-fire; `next_run_at` is advanced before the
  trigger runs, so a crash is a missing run, not a duplicate.
- **Cooperative cancellation.** `cancel_task_run` / `request_cancellation` cancel
  a run and every unresolved task — including `RUNNING` siblings — and bump the
  fencing token so a handler that is still running has its late result discarded
  rather than resurrecting a cancelled task. A cancel-vs-completion race test on
  real PostgreSQL proves exactly one of the two wins.
- **Execution history / event log.** `TaskRunEvent` is an append-only record of
  every task-run state change, emitted from `core/repositories/transitions.py` on
  each transition (claim, finish, cancel, requeue, reclaim, promote, retry, skip).
  A lost race records nothing. `GET /api/v1/runs/<id>/events/` returns a run's
  events, oldest first, with the same per-user visibility as the run detail.
- **Manual re-run of a failed task.** `requeue_failed_task_run` resets a `FAILED`
  task run to `PENDING` with a fresh attempt and reverts a `FAILED` run — never a
  `SUCCEEDED` or `CANCELLED` one. `POST /api/v1/task-runs/<id>/requeue/` exposes
  it; a task that is not `FAILED` is a 409, and two operators racing the same task
  re-run it exactly once (`SELECT FOR UPDATE`).
- **Saga-style compensation.** A task may declare `compensate_for: <key>`. Such a
  task is held out of the forward DAG — it is not materialised when the run starts
  — and runs only when the run fails, to undo the tasks that had *succeeded*.
  Compensations run strictly one at a time in reverse dependency order (a
  dependent is undone before its dependency), and the run is held `RUNNING` until
  the rollback settles before it finishes `FAILED`.

### Fixed

- **Skip and promotion propagation now completes in one scheduler pass on any
  backend.** `advance_workflow_run` iterated task runs in database-return order,
  which is insertion order on SQLite but unspecified on PostgreSQL — so a dependant
  processed before the dependency it waits on could miss a just-applied skip and
  stay `PENDING`. It now walks `definition.graph.topological_order()`, so the
  result no longer depends on row order.

## [0.7.2] - 2026-10-22

Resilience verification for the v0.7.1 reconnection work: every process is now
proven, individually, to survive a broker restart *and* a PostgreSQL restart
with no process restart.

### Changed

- **Resilience verification tightened.** The `docker` CI job now proves, per process,
  that the worker, publisher and scheduler survive a broker restart *and* a
  PostgreSQL restart with no process restart. Each process is asserted individually:
  the worker logs `reconnecting in`, the publisher logs `Publisher recovered`, the
  scheduler logs `Scheduler recovered`, container IDs are diffed before/after (a
  manual restart would change them), and a freshly queued run must reach `SUCCEEDED`.
  The recovery-log greps poll with deadlines rather than sleeping. `docs/architecture/
  failure-model.md` gains a [reconnection matrix](#reconnection-matrix) mapping every
  process × fault to its named test; anything without a test is marked UNVERIFIED.

## [0.7.1] - 2026-10-21

Closes the two gaps Phase 6 left open: the broker connection was single-shot, and
the dispatch queue was a classic queue.

### Added

- **Automatic reconnection.** `Worker.run()` is a loop: a connection-level failure
  is backed off exponentially with jitter (capped at 30s) and retried, and the
  connection, channel, queue declaration and consumer are all re-established. No
  process restart. `stop()` interrupts the wait, so SIGTERM during an outage exits
  promptly.
- **Publisher sweep retry.** The publisher backs off on the same curve and retries
  the sweep; rows stay pending until the broker confirms them.
- **Quorum dispatch queue.** The queue is declared with `x-queue-type: quorum`, so
  a publisher confirm means the message is fsynced through Raft, not merely
  buffered. `core.broker_rabbit.declare_dispatch_queue` is the single declaration
  every component uses.
- **`QueueTypeMismatchError`.** Declaring an existing classic queue as quorum
  raises it with the delete-and-redeclare procedure, instead of letting a raw
  `PRECONDITION_FAILED` channel closure look like a transient fault.

### Changed

- **An ack failure is no longer swallowed.** If the channel dies between the
  durable write and the ack, the exception propagates so the broker requeues the
  message. Swallowing it would report a lost ack as a success.
- The `docker` CI job no longer restarts the consumers after a broker outage. It
  triggers a workflow **while the broker is down**, compares container IDs before
  and after (a manual restart would change them), and requires the run to reach
  `SUCCEEDED` on its own.
- `docs/architecture/failure-model.md`: mode 14 (broker reconnection) moves to
  **verified**, and mode 17 (broker loses a confirmed message) moves to **verified
  for abrupt termination on a single node** — both only because a named test now
  proves them.
- `docs/architecture/execution-model.md` documents the quorum queue, the
  single-node caveat, and the migration procedure.

### Fixed

- **The scheduler exited on a database restart.** It held one connection for its
  whole life, so `restart db` produced `OperationalError: terminating connection
  due to administrator command` and killed the process — every run then stayed
  `pending`. The scheduler and publisher now discard the connection
  (`core.db.reset_connections`) and back off; the worker does the same per message
  and re-raises so the broker requeues it. Found by the docker job, which asserts
  the pipeline still does real work after the restart rather than only that the
  probe is green.
- **A closed connection was treated as a clean stop.** pika's
  `start_consuming()` returns normally when the connection closes under it, so the
  first reconnect implementation still exited the process on a broker restart. The
  loop now treats "returned without a stop request" as a lost connection.

### Known limitations

- Quorum queues on one node give crash-safe durability, not high availability;
  there is no replica to fail over to.
- Reconnection is per process: a worker that was *killed* still needs its
  supervisor to restart it.

## [0.7.0] - 2026-10-21

Phase 6 — Reliability. `FAILED` stops being a dead end and `DEAD` becomes the
terminal state a human must resolve.

### Added

- **Retry policy** (`core/domain/retry.py`, pure domain, no Django import):
  `RetryPolicy` (validated at construction), `get_retry_policy(config)` reading the
  per-task `config["retry"]` block, and `calculate_backoff` — exponential growth
  with a hard cap and optional full jitter.
- **`TaskRunState.DEAD`** and a dead-letter lifecycle. The task machine now has 12
  edges: `reap_retry` (RUNNING→RETRYING), `reap_dead` (RUNNING→DEAD) and `bury`
  (FAILED→DEAD) replace the old `reap_lease` (RUNNING→READY). A dead worker is a
  *consumed attempt*, so a crash loop cannot run forever uncounted.
- **Scheduler retry lifecycle** (`core/repositories/transitions.py`):
  `schedule_retries` moves FAILED into RETRYING (or DEAD when exhausted),
  `promote_due_retries` promotes RETRYING back to READY only once the backoff has
  elapsed, and `requeue_dead_task_run` is the manual recovery path.
  `schedule_once` runs reap → schedule_retries → promote_due_retries → advance.
- **`manage.py requeue_dead <task_run_id>`**: the operator's way out of `DEAD`. It
  exits non-zero for a missing row, a malformed id, or a task that is not DEAD.
- **Persisted handler results**: the worker writes a handler's return value into
  `TaskRun.result` on success, and leaves it untouched on failure so the next
  attempt starts clean.
- **Idempotency keys**: a task may declare `config["idempotency_key"]`. A dispatch
  whose key already has a `SUCCEEDED` result reuses it instead of executing again.
  Best-effort by design — it is not a lock (see the failure model).
- **Failure-injection tests** (`tests/integration/test_failure_injection.py`) on
  real PostgreSQL + RabbitMQ: SIGKILL a worker mid-task and prove the reaper
  retries it to `SUCCEEDED`; SIGKILL the scheduler and prove a fresh pass cannot
  double-dispatch; lose a publisher confirm and prove the outbox row stays pending
  and is delivered exactly once.
- **`docs/architecture/failure-model.md`**: every failure mode mapped to a named
  test, with the untested ones marked UNVERIFIED (clock skew, broker durability,
  in-flight sibling cancellation).

### Changed

- `core/models.TaskRun` gained `scheduled_at`, `result` and `idempotency_key`, plus
  the `taskrun_scheduled_iff_retrying` CHECK constraint (migration `0005`).
- `derive_workflow_run_state` treats `DEAD` as a permanent failure and
  `FINISHED_TASK_STATES` now means "terminal", not "done or failed".
- The `docker` CI job injects infrastructure faults: it stops RabbitMQ and asserts
  readiness returns 503 naming `rabbitmq`, starts it and asserts 200; restarts
  PostgreSQL and asserts recovery; then runs the end-to-end sample again, so a
  green probe is never the only claim.
- The publisher's confirms and the worker's acks are unchanged; what changed is
  what happens *after* a failure.

### Fixed

- `transition_task_run` cleared `scheduled_at` only for terminal targets, so
  `RETRYING → READY` left the column set and violated the new CHECK constraint.
  The column now defaults to NULL on every transition; only the RETRYING branch
  sets it. Found by the retry-lifecycle tests, not by reading the code.
- `tests/unit/test_worker.py` and `tests/integration/test_worker.py` both
  registered a handler named `test.slow`, so a bare `pytest` (the configured
  `testpaths = ["tests"]`) aborted during collection. CI never saw it because it
  runs the two trees in separate processes. The integration handlers are now
  namespaced `test.integration.*`.

### Known limitations

- The worker and publisher do not reconnect to RabbitMQ; a broker restart requires
  restarting the processes. Nothing is lost (the outbox row stays pending and the
  lease is reaped), and the docker job restarts them explicitly.
- Broker durability is classic-queue durability: `delivery_mode=2` to durable
  queues survives a clean restart, not an abrupt failure before fsync. A quorum
  queue is the fix.
- Idempotency keys do not serialise two concurrent executions of the same key.

## [0.6.1] - 2026-10-21

Closes the Phase 5 verification gaps the owner flagged before Phase 6.

### Added

- A real SIGTERM test (`tests/integration/test_worker_sigterm.py`): `manage.py
  worker` is started as a subprocess against PostgreSQL + RabbitMQ, a task whose
  `wait_for_file` handler blocks on a file is dispatched, SIGTERM is sent, and the
  test asserts the in-flight task finishes and is acked, the process exits 0, and no
  task is left `RUNNING` with a live lease. Waits poll with a deadline.
- `core/worker/builtin.py`: `wait_for_file`, a demo/test handler that blocks until a
  file appears (no timer race).
- `core/management/commands/e2e_token.py`: creates an e2e staff user and writes its
  DRF token for the REST API end-to-end step.
- `core/management/commands/healthcheck.py`: a real DB + broker health check for the
  non-web processes (scheduler, publisher, worker), which run no HTTP server.

### Changed

- The compose `scheduler`, `publisher` and `worker` healthchecks now use
  `manage.py healthcheck` (database + broker) instead of a port-8000 probe that
  always failed for them. The docker CI job asserts `healthy`, not only
  `State.Status`.
- The compose CI job now drives the flow through the REST API (auth → publish →
  trigger → poll to `succeeded`) in addition to the in-process `sample_run`.
- `docs/development/PROGRESS.md` refreshed: real AMQP readiness probe, accurate
  service count, and limitations (the e2e suite and the AMQP probe are no longer
  "not done").
- `docs/development/DECISIONS.md`: records the earlier direct push of `a8a5337` to
  `main` as a process lapse; branch protection now sets `enforce_admins` so an
  administrator cannot bypass the required checks.

## [0.6.0] - 2026-10-21

Phase 5 (Distributed Workers) is complete. A dispatch is now executed end to end:
the worker consumes it, claims the task run with a fencing token, runs a trusted
handler, writes the durable outcome, and only then acks. The scheduler also
completes the workflow run when its tasks resolve, so a correct run no longer
stays `RUNNING` forever.

### Added

- `core/worker/registry.py`: a trusted, in-process handler registry. A message
  names a `task_type`; nothing in it is imported or executed as code. An unknown
  type fails the task.
- `core/worker/builtin.py`: `noop`, `echo`, `add` — the only handlers a message can
  run.
- `core/worker/consumer.py`: the worker — manual ack (`auto_ack=False`), prefetch,
  a lease heartbeat thread, a graceful stop, and a `process` method separated from
  pika for unit testing.
- `core/repositories/transitions.py`: `renew_lease`, the heartbeat's conditional
  `UPDATE` (matches only while the token still owns the row).
- `manage.py worker`: consumes dispatches, with SIGTERM/SIGINT stopping after the
  in-flight message.
- `manage.py sample_run` and a `worker` compose service: the end-to-end sample
  (echo → add → noop) the docker CI job runs.
- Scheduler run completion: `advance_workflow_run` derives the run state from its
  task runs and completes `RUNNING → SUCCEEDED/FAILED`.

### Reliability

- Ack only after the durable outcome; a failed write nacks for requeue.
- Duplicate delivery executes once (the claim only matches a READY row).
- A stale result from a reaped worker matches zero rows and is discarded.
- A killed worker's lease is reclaimed; the new generation owns the task.
- A lost ack redelivers the message (broker at-least-once).

### Tests

- Unit: success, handler failure, unknown type, duplicate delivery, finish failure,
  stale finish, malformed payload, heartbeat, killed-worker stale rejection,
  registry rules, run completion.
- Integration (PostgreSQL + RabbitMQ): end-to-end consume, duplicate delivery, lost
  ack redelivery, graceful shutdown.
- Docker job: `sample_run` must reach `SUCCEEDED`.

## [0.5.1] - 2026-10-21

Closes the Phase 4 gap: the publisher's guarantees are broker semantics, so they
are now verified against a real RabbitMQ in CI rather than against a fake.

### Added

- `tests/integration/test_rabbit_publisher.py`: five scenarios against a live
  broker — publisher confirms and arrival (consume and assert the payload), an
  unroutable publish leaves the row pending, a broker outage (dead port) leaves
  rows pending and recovery drains them exactly once, a row committed before any
  publish is delivered by a fresh publisher, and two publishers never publish the
  same row twice. No sleeps: the consumer polls `basic_get` with a deadline.
- The CI `test` job runs a `rabbitmq:3.13-alpine` service with a healthcheck and
  `REQUIRE_RABBITMQ=1`, so a missing broker fails instead of skipping.

### Changed

- `RabbitPublisher` publishes with `mandatory=True`: an unroutable message is
  returned (`UnroutableError` → `PublisherError`) instead of silently dropped, so
  the outbox row stays pending until a worker queue is bound.
- The readiness probe opens a real AMQP connection instead of a TCP connect.
- `manage.py publisher` logs and retries a failed sweep, so a broker outage leaves
  rows pending and a later sweep drains them instead of killing the process.

### Verified

- `RabbitPublisher` against a live broker in CI (previously structural only). The
  owner-side verification note is removed.

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
