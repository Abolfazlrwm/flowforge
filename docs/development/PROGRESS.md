# Progress

## Current Phase

**Phase 8 — Security & Multi-Tenancy: RELEASED (v0.9.0).** Merged as `4febc05`
(PR #38) and tagged `v0.9.0`. Organizations and memberships with
`owner/admin/member/viewer` roles; per-organization scoping by construction
([ADR-014](../adr/ADR-014-multi-tenancy-shared-schema.md)); per-tenant definition
and idempotency uniqueness; hashed, scoped, rotatable API keys; an append-only
audit log enforced by a database trigger; secret redaction; a tenant-isolation
suite; a STRIDE threat model ([`docs/security/threat-model.md`](../security/threat-model.md));
and `bandit` + `pip-audit` in CI.

**`v0.9.1` (in progress on `chore/phase8-followups`)** adds membership
management (`GET/POST /members/`, `PATCH/DELETE /members/<id>/`) with the
owner/admin rules, tenant-isolation tests and audit entries; rewrites this
record; and adds `make test-pg`.

**Phase 9 — Observability & Performance (v0.10.0)** is next.

## Resume point

If this session is resumed cold, read this section first.

| Question | Answer |
|----------|--------|
| Where is the work | `main` is at `v0.9.0` (`4febc05`). `chore/phase8-followups` is open for `v0.9.1`. |
| What is released | `v0.1.0` … `v0.9.0`, including the `v0.5.1`, `v0.6.1`, `v0.7.1` and `v0.7.2` gap releases. |
| Framework | **Django 5.2 LTS** ([ADR-010](../adr/ADR-010-django-5-2-lts.md)) and **DRF 3.18.3**. Python 3.13 in CI/Docker; the package requires >= 3.11. |
| Message broker | **RabbitMQ via pika** ([ADR-011](../adr/ADR-011-custom-scheduler-and-transactional-outbox.md)). The dispatch queue is a **quorum** queue; the worker reconnects through broker outages. |
| Tenancy | **Shared schema, per-organization scoping** ([ADR-014](../adr/ADR-014-multi-tenancy-shared-schema.md)). Every tenant read goes through `OrganizationScopedManager.for_organization`; another tenant's id is a 404. |
| What is next | Phase 9 — Observability & Performance (v0.10.0). |
| What blocks Phase 9 | **Nothing.** |
| What else is open | Nothing pending an owner decision. Release tags are automatic on green CI; only `v1.0.0` needs owner approval ([DECISIONS.md](DECISIONS.md)). |

## Status

| Item | State |
|------|-------|
| Branch | `main` at `v0.9.0`; `chore/phase8-followups` for `v0.9.1` |
| Phase 0 | **MERGED** as `97c17bf` (PR #1), tagged `v0.1.0` |
| Phase 1 | **MERGED** as `869720f`, tagged `v0.2.0` |
| Phase 2 | **MERGED** as `d31abf3`, tagged `v0.3.0` |
| Phase 3 | **MERGED** as `405cb3d`, tagged `v0.4.0` |
| Phase 4 | **MERGED** as `a68ac2a` (PR #25), tagged `v0.5.0`; gap `v0.5.1` (PR #27) |
| Phase 5 | **MERGED** as `6b0564f` (PR #28), tagged `v0.6.0`; gap `v0.6.1` (PR #30) |
| Phase 6 | **MERGED** as `3210001` (PR #31), tagged `v0.7.0` |
| Resilience gap releases | `v0.7.1` (PR #33: reconnection, quorum queue) and `v0.7.2` (PR #34: restart-survival proof) |
| Phase 7 | **MERGED** (PR #36), tagged `v0.8.0` (`89eb600`); gap `v0.8.1` (PR #37, `ed25c15`) |
| Phase 8 | **MERGED** (PR #38) as `4febc05`, tagged `v0.9.0` |
| `v0.9.1` follow-ups | `chore/phase8-followups`: membership API, this rewrite, `make test-pg` |
| CI jobs | `lint` (ruff, ruff format, mypy, **bandit**, **pip-audit**), `test`, `schema`, `docs`, `docker` |
| Test database in CI | **PostgreSQL 16** for the unit and integration suites; Redis 7 and RabbitMQ 3.13 services for integration |
| Docker | Verified in CI (build, compose up, health probes, e2e, failure injection); owner decision 2026-10-19 |
| Tags | `v0.1.0` … `v0.9.0`, annotated |
| GitHub Releases | Published for `v0.1.0` … `v0.9.0` |
| Branch protection | `main`: `lint`, `test`, `docker` required, strict, no force push, `enforce_admins` |
| Django | **5.2 LTS** ([ADR-010](../adr/ADR-010-django-5-2-lts.md)) |
| DRF | **3.18.3** (fixes CVE-2026-114136/114137/114138) |
| Python | 3.13 in CI/Docker; `requires-python >= 3.11` |
| PostgreSQL | 16 (CI service and compose) |
| Redis | 7 (CI service and compose); client `redis>=5,<6` |
| RabbitMQ | 3.13 (CI service and compose); quorum dispatch queue |

## Completed

### Phase 0 — Engineering foundation

- Django project, settings package, PostgreSQL/Redis/RabbitMQ wiring, health
  probes (live + ready), Docker Compose, multi-stage non-root image.
- Ruff, mypy strict, pytest + coverage gate (80%), pre-commit, `uv.lock`,
  SHA-pinned Actions, Dependabot, issue/PR templates, branch protection.
- `.gitattributes` forces LF so a Windows checkout matches CI.

### Phase 1 — Domain modeling

- `core/domain/` is Django-free, enforced by an AST scan and a subprocess
  re-import with `django` blocked.
- `WorkflowRunState` and `TaskRunState` as `StrEnum`; `StateMachine` validates
  duplicate edges, dead ends and edges out of terminal states at import time.
- `WORKFLOW_RUN_MACHINE` has 5 edges and 3 terminal states;
  `TASK_RUN_MACHINE` has 14 edges and 4 terminal states (`SUCCEEDED`,
  `SKIPPED`, `DEAD`, `CANCELLED`).
- Persistence models with UUID PKs, `CHECK` constraints and named indexes;
  constraints are proven through `QuerySet.update()` so Python guards are
  bypassed.
- Atomic conditional `UPDATE` transitions with fencing tokens; Hypothesis
  state-machine tests; PostgreSQL concurrency tests with a `threading.Barrier`.
- Traps recorded: `TaskRunState.CANCELLED == WorkflowRunState.CANCELLED` is
  `True` (use `is`/`isinstance`); `attempt` is 1-based; `scheduled_at` is valid
  only while `RETRYING`.

### Phase 2 — Workflow definitions

- Frozen `TaskDefinition`/`WorkflowDefinition`; config validated as JSON-shaped
  at construction; `DependencyGraph` with iterative cycle detection.
- Draft 2020-12 schema with JSONPaths; domain errors for duplicate keys,
  dangling references and cycles.
- `publish_definition` is idempotent by fingerprint, writes the workflow and its
  tasks in one transaction, and raises `DefinitionConflictError` for different
  content under an existing version.
- `load_definition` re-validates on the way out.
- Hypothesis DAG property tests and PostgreSQL publish-race tests.

### Phase 3 — REST API

- `/api/v1/`: workflows (publish/list/detail), runs (trigger/list/detail),
  task-run requeue, run events, token/JWT auth, cursor pagination.
- One error envelope with a stable `code`; domain errors mapped in
  `api/errors.py`, not in views.
- OpenAPI schema committed to `docs/api/openapi.yaml`; the `schema` job fails on
  drift.

### Phase 4 — Scheduler, outbox and publisher

- Custom scheduler and transactional outbox ([ADR-011](../adr/ADR-011-custom-scheduler-and-transactional-outbox.md)).
- `advance_workflow_run` promotes eligible tasks, starts the run and writes one
  outbox row per dispatch generation in one transaction.
- `create_dispatch` pre-checks existence before INSERT: catching the UNIQUE
  violation alone aborted an open SQLite transaction.
- Publisher drains the outbox with `FOR UPDATE SKIP LOCKED`, publisher confirms,
  and marks a row delivered only after the broker acks.
- `v0.5.1` verified the publisher against a real RabbitMQ service (confirms,
  mandatory routing, outage/recovery, crash-window redelivery, two publishers).

### Phase 5 — Distributed workers

- Trusted in-process handler registry; a dispatch names a `task_type`, never
  imports code.
- Manual ack, prefetch, lease heartbeat, graceful SIGTERM, fencing tokens.
- `manage.py worker`; the compose e2e drives echo → add → noop to `SUCCEEDED`
  in-process and through the REST API.

### Phase 6 — Reliability

- `RetryPolicy` with exponential backoff, a cap and optional jitter.
- `TaskRunState.DEAD` and the 14-edge task machine; a reaped lease is a
  **consumed attempt** (RUNNING → RETRYING or DEAD), never straight back to
  READY.
- `schedule_retries`, `promote_due_retries`, `requeue_dead_task_run`; the worker
  persists handler results and reuses a prior `SUCCEEDED` result for an
  idempotency key.
- Failure-injection tests (SIGKILL a worker, SIGKILL the scheduler, lose a
  publisher confirm) on real PostgreSQL + RabbitMQ.
- `docs/architecture/failure-model.md` maps each failure mode to a test or marks
  it UNVERIFIED.

### Phase 7 — Advanced workflows

- Branching on a sandboxed expression language; fan-in (`all`/`any`).
- Cron `ScheduledTrigger` with no double-fire across instances.
- Cooperative cancellation (`request_cancellation` cancels non-terminal task
  runs) and `SKIPPED` tasks.
- Append-only `TaskRunEvent` log with a read endpoint; manual re-run of a failed
  task; saga compensation.
- `v0.8.1` added compose e2e for cron and saga and made the event insert
  transactional with its state change ([ADR-013](../adr/ADR-013-event-log-is-transactional.md)).

### Phase 8 — Security & Multi-Tenancy

- `Organization`/`Membership` with `owner/admin/member/viewer`; a unique role
  per `(organization, user)`.
- `organization` FK on every tenant model; `OrganizationScopedManager` as the
  read path; another tenant's id is a 404.
- Per-org uniqueness: `(organization, name, version)` and
  `(organization, user, key)`.
- Reversible migrations `0011` (schema) and `0012` (default-org backfill).
- API keys: `ff_<hex-prefix>_<hex-secret>`, only a `make_password` hash stored,
  scopes `read < write < admin`, expiry, rotation, revocation, shown once.
  `ApiKeyAuthentication` fixes the tenant to the key and refuses an orphaned
  key; a key never inherits the staff cross-tenant bypass.
- Append-only `AuditLog` with a database trigger (`0015`) rejecting `UPDATE` and
  `DELETE`; entries for publish, trigger, requeue and key lifecycle; metadata
  redacted.
- `core.security.redact_mapping` redacts by key name and by `secret: true`.
- Tenant-isolation suite, role matrix, API-key lifecycle tests.
- STRIDE threat model and ADR-014.
- `bandit` + `pip-audit`; the latter forced DRF 3.17.2 → 3.18.3 and
  certifi → 2026.10.19.

### v0.9.1 — Phase 8 follow-ups

- **Membership management**: `GET/POST /api/v1/members/`,
  `PATCH/DELETE /api/v1/members/<uuid>/`.
  - Only owner/admin may call them; an admin cannot modify an owner and cannot
    grant the owner role; the last owner cannot be removed or demoted.
  - Every real change writes an audit entry (`member.added`,
    `member.role_changed`, `member.removed`).
  - Another tenant's membership is a 404.
- This record was rewritten against the code and CI.
- `make test-pg` runs the full unit suite on the compose PostgreSQL database;
  `make db-up` starts it. `docs/development/setup.md` explains why SQLite is not
  the authority for locking, ordering or trigger semantics.

## Verification

### In CI (authoritative)

Phase 8 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39921080486
(v0.9.0 — 645 unit tests on PostgreSQL + 29 integration tests green; `lint`
includes `bandit` and `pip-audit`; the docker job drove the cron and saga e2e
through the real stack)

v0.9.1 run: recorded on the `chore/phase8-followups` pull request once green.

### Locally (Windows, Python 3.13.14)

| Check | Result |
|-------|--------|
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS |
| `uv run mypy flowforge core api --ignore-missing-imports` | PASS (68 files) |
| `uv run pytest tests/unit` | 661 passed (SQLite) |
| `manage.py makemigrations --check --dry-run` | No changes detected |
| `uv run mkdocs build --strict` | PASS |
| `uv run bandit -r core api flowforge -c pyproject.toml` | 0 issues |
| `uv run pip-audit` | No known vulnerabilities |

`make test-pg` is the local PostgreSQL run and needs Docker. CI already runs the
full unit suite on PostgreSQL, so a machine without Docker is not unverified —
it is the same suite against the same engine in CI.

## Known limitations

### Architecture

1. **Idempotency keys are best-effort, not a lock.** A dispatch whose key already
   has a `SUCCEEDED` result reuses it, but two concurrent executions of the same
   key can both run. At-least-once delivery is the contract.
2. **Readiness checks are not exhaustive.** RabbitMQ uses a real AMQP
   open/close, Redis a cache round-trip, PostgreSQL `SELECT 1`; none asserts the
   broker's vhost or permissions beyond a successful handshake.
3. **`make demo` probes health, not a workflow.** The e2e run is
   `manage.py sample_run` and the REST API flow in the compose CI job.
4. **`publish_definition` does not serialize concurrent first-publishes.**
   `select_for_update()` cannot lock a row that does not exist; the unique
   constraint picks a winner and the loser sees `IntegrityError`. The database
   stays correct; the caller retries.
5. **The MkDocs build does not validate Mermaid syntax.** A wrong diagram renders
   as an error box and passes `mkdocs build --strict`.
6. **PostgreSQL RLS is not used.** Application-level scoping is the tenant
   boundary (ADR-014); RLS is a follow-up.
7. **Staff is a cross-tenant read bypass.** Intentional and narrow; an API key
   never inherits it.
8. **The audit log is not tamper-evident against a database owner.** The trigger
   blocks `UPDATE`/`DELETE` through SQL, but a superuser can drop it.
9. **Secret redaction is key-name based.** A secret under an innocuous key is not
   redacted unless the mapping declares `secret: true`.
10. **Throttling is per-process.** A distributed deployment needs a shared cache;
    the test suite uses a local cache.

### Tooling and deployment

11. **Base image pinned by tag, not digest.** `python:3.13-slim` can drift.
12. **No dedicated `tests/e2e/` directory.** End-to-end coverage lives in the
    compose CI job and `tests/integration/test_worker_sigterm.py`; it is CI-only
    because the maintainer's Docker daemon is offline (owner decision
    2026-10-19).
13. **Coverage is a line count.** The gate is 80%; the exact number is reported by
    CI and is not repeated here, because a number in prose goes stale.
14. **Pre-commit hooks use `language: system`**, so they depend on the local
    environment.
15. **Compose bind-mounts the source over `/app`**, hiding the image's
    `staticfiles/` output. Harmless with `DEBUG=True`, wrong for a
    production-like run.
16. **The migration gate needs Docker Compose v2.20+** for
    `service_completed_successfully`; the stack is not gated on the Compose
    version.
17. **The name "FlowForge" is conflicted** (npm reserved, PyPI `flow-forge` taken,
    a prior company now FlowFuse). Retained by owner-delegated decision
    ([ADR-009](../adr/ADR-009-project-name-evaluation.md)).

### Dependencies

18. **Python stays at 3.13.** Django 5.2 supports 3.14 only from 5.2.8; a 3.14 CI
    matrix entry and a matching base image are not in place.
19. **redis-py stays below 6.** The cache backend is verified against 5.x before a
    major bump.

## Dependency decisions

| Package | Version | Decision |
|---------|---------|----------|
| Django | 5.2.18 | LTS; [ADR-010](../adr/ADR-010-django-5-2-lts.md) |
| djangorestframework | 3.18.3 | **Security fix** for CVE-2026-114136/114137/114138 (pip-audit) |
| certifi | 2026.10.19 | **Security fix** for PYSEC-2026-4320 (pip-audit) |
| psycopg | 3.2.x (`[binary]`) | PostgreSQL driver |
| pika | 1.3.x | RabbitMQ client ([ADR-011](../adr/ADR-011-custom-scheduler-and-transactional-outbox.md)) |
| redis | >= 5, < 6 | Cache backend compatibility |
| croniter | 5.x | Cron expression parsing |
| structlog + django-structlog | 24.x / 9.x | Structured logging |
| Python | 3.13 in CI/Docker; `>= 3.11` | 3.14 deferred (limitation 18) |
| bandit + pip-audit | dev extra | Required `lint` checks |

## Blockers

None.

## Next step

Phase 9 — **Observability and Performance (v0.10.0)**, per
[`ROADMAP.md`](ROADMAP.md):

- structured JSON logging with correlation IDs propagated API → outbox → broker
  → worker;
- a Prometheus metrics endpoint (queue depth, outbox lag, task latency, retries,
  lease expirations, DEAD count) per process;
- OpenTelemetry tracing API → scheduler → publisher → worker, with a
  trace-propagation test;
- Grafana dashboards as code and a compose `observability` profile (Prometheus,
  Grafana, OTel collector);
- load tests with a documented reproducible command and a real measured
  benchmark report;
- profile and fix at least one measured bottleneck, with before/after numbers.

## Ordering note

A CI URL is added only after the run is green. The v0.9.0 URL above is the run
that merged as `4febc05`; the v0.9.1 URL is added in a follow-up commit on the
same pull request once its run passes, because committing a URL before the run
exists would be a claim rather than a verification.
