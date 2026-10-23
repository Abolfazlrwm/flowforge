# Progress

## Current Phase

**Phase 11 — Production Hardening (v1.0.0): IN PROGRESS** on
`feat/phase11-hardening`. The operational surface: a security review
(gitleaks over full history, Trivy image scanning, a STRIDE threat model), a
migration-safety review with a CI upgrade test, a Kubernetes Helm chart with
`helm lint` + `kubeconform` validation, a backup/restore runbook with a CI
restore drill, high-availability guidance, an end-to-end suite (`tests/e2e/`)
against the Compose stack, and a signed, SBOM'd supply chain. **`v1.0.0` needs
the owner's explicit approval before it is tagged** ([DECISIONS.md](DECISIONS.md));
the code merges on green CI, the tag waits.

**Phase 10 — SDK, CLI and Documentation: RELEASED (v0.11.0).** Merged from
`feat/phase10-sdk-cli` (PR #42). `flowforge-client` — a dependency-free Python
client and the `flowforge` command — plus four runnable examples and three new
documentation pages (SDK guide, CLI reference, complete API reference).

**Phase 9 — Observability & Performance: RELEASED (v0.10.0).** Merged as
`6a58fdc` (PR #41). One correlation id and one trace follow a run from the HTTP
request through the outbox, the broker and the worker; Prometheus metrics are
exposed per process; Grafana dashboards and a compose `observability` profile are
code; a Locust load test with committed raw results gives the first measured
benchmark; and one profiled bottleneck was fixed with before/after numbers.

**Phase 8 — Security & Multi-Tenancy: RELEASED (v0.9.0).** Merged as `4febc05`
(PR #38) and tagged `v0.9.0`. Organizations and memberships with
`owner/admin/member/viewer` roles; per-organization scoping by construction
([ADR-014](../adr/ADR-014-multi-tenancy-shared-schema.md)); per-tenant definition
and idempotency uniqueness; hashed, scoped, rotatable API keys; an append-only
audit log enforced by a database trigger; secret redaction; a tenant-isolation
suite; a STRIDE threat model ([`docs/security/threat-model.md`](../security/threat-model.md));
and `bandit` + `pip-audit` in CI.

**`v0.9.1`** added membership management (`GET/POST /members/`,
`PATCH/DELETE /members/<id>/`) with the owner/admin rules, tenant-isolation tests
and audit entries; rewrote this record; and added `make test-pg`.

**Phase 11 — Production Hardening (v1.0.0)** is the current work; see the top of
this section. It is the last planned phase, and **`v1.0.0` needs the owner's
explicit approval before it is tagged** ([DECISIONS.md](DECISIONS.md)).

## Resume point

If this session is resumed cold, read this section first.

| Question | Answer |
|----------|--------|
| Where is the work | `main` is at `v0.11.0`. **Phase 11 is in progress** on `feat/phase11-hardening`. |
| What is released | `v0.1.0` … `v0.11.0`, including the `v0.5.1`, `v0.6.1`, `v0.7.1`, `v0.7.2`, `v0.8.1` and `v0.9.1` gap releases. |
| Framework | **Django 5.2 LTS** ([ADR-010](../adr/ADR-010-django-5-2-lts.md)) and **DRF 3.18.3**. Python 3.13 in CI/Docker; the package requires >= 3.11. |
| Message broker | **RabbitMQ via pika** ([ADR-011](../adr/ADR-011-custom-scheduler-and-transactional-outbox.md)). The dispatch queue is a **quorum** queue; the worker reconnects through broker outages. |
| Tenancy | **Shared schema, per-organization scoping** ([ADR-014](../adr/ADR-014-multi-tenancy-shared-schema.md)). Every tenant read goes through `OrganizationScopedManager.for_organization`; another tenant's id is a 404. |
| Observability | `core/observability/` — correlation ids in `context.py`, one registry per process in `metrics.py`, module-local OTel provider in `tracing.py`. See [`deployment/observability.md`](../deployment/observability.md). |
| Client | **`sdk/`** — `flowforge-client`, a separate distribution with no runtime dependencies, exposing the `flowforge` CLI. See [`sdk/index.md`](../sdk/index.md) and [`cli.md`](../cli.md). |
| What is next | Finish Phase 11 (Production Hardening), then tag `v1.0.0` **with the owner's approval**. |
| What blocks Phase 11 | **Nothing.** |
| What else is open | `v1.0.0` needs the owner's explicit approval before it is tagged; every other release tag is automatic on green CI. `flowforge-client` is not published to PyPI (limitation 27). |

## Status

| Item | State |
|------|-------|
| Branch | `main` at `v0.11.0`; **Phase 11 in progress** on `feat/phase11-hardening` |
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
| `v0.9.1` follow-ups | **MERGED** (PR #40) as `a00e267`, tagged `v0.9.1` |
| Phase 9 | **MERGED** (PR #41) as `6a58fdc`, tagged `v0.10.0` |
| Phase 10 | **MERGED** (PR #42), tagged `v0.11.0` |
| Phase 11 | **IN PROGRESS** on `feat/phase11-hardening` (not yet merged) |
| CI jobs | `lint` (ruff, ruff format, mypy, **bandit**, **pip-audit**), `test`, `schema`, `docs`, `sdk`, `docker`, `loadtest`, `secrets` (gitleaks), `supply-chain` (Trivy + CycloneDX SBOM + cosign), `k8s` (helm lint + kubeconform) |
| Other workflows | `release.yml` (attaches the SBOM to a published release), `pages.yml` (publishes the MkDocs site), `k8s-e2e.yml` (manual kind smoke test) |
| Test database in CI | **PostgreSQL 16** for the unit and integration suites; Redis 7 and RabbitMQ 3.13 services for integration |
| Docker | Verified in CI (build, compose up, health probes, e2e, failure injection, **observability profile**); owner decision 2026-10-19 |
| Tags | `v0.1.0` … `v0.11.0`, annotated |
| GitHub Releases | Published for `v0.1.0` … `v0.11.0` |
| Branch protection | `main`: `lint`, `test`, `docker` required, strict, no force push, `enforce_admins` |
| Django | **5.2 LTS** ([ADR-010](../adr/ADR-010-django-5-2-lts.md)) |
| DRF | **3.18.3** (fixes CVE-2026-114136/114137/114138) |
| Python | 3.13 in CI/Docker; `requires-python >= 3.11` |
| PostgreSQL | 16 (CI service and compose) |
| Redis | 7 (CI service and compose); client `redis>=5,<6` |
| RabbitMQ | 3.13 (CI service and compose); quorum dispatch queue |
| Prometheus client | `prometheus-client` 0.26.0 |
| OpenTelemetry | `opentelemetry-api`/`sdk` 1.45.1, OTLP/HTTP exporter 1.45.1 |
| Locust | 2.47.0 (dev extra) |
| Client | `flowforge-client` 0.11.0, **no runtime dependencies** |

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

### Phase 9 — Observability & Performance

- **Correlation ids.** `core.observability.context` wraps structlog's
  contextvars. The API binds `request_id` (or generates one); the id is stored in
  `WorkflowRun.correlation_id` and `OutboxMessage.correlation_id` (migration
  `0016`) and repeated in the dispatch payload; the worker clears and rebinds the
  context per message. JSON logs carry it on every line.
- **Prometheus metrics.** One registry per process. Process-local counters and
  histograms (`flowforge_task_latency_seconds`, `flowforge_task_retries_total`,
  `flowforge_lease_expirations_total`); durable-state gauges computed at scrape
  time so they cannot drift (`flowforge_workflow_runs`, `flowforge_task_runs`,
  `flowforge_outbox_pending`, `flowforge_outbox_lag_seconds`,
  `flowforge_dead_tasks`, `flowforge_scheduled_triggers_due`).
  - `GET /api/v1/metrics/` on the web process (unauthenticated, aggregate only).
  - `--metrics-port` on the scheduler, publisher and worker; `0` or empty
    disables it. The compose stack sets 9101/9102/9103, published on loopback.
- **OpenTelemetry tracing.** Module-local provider (so a test can install an
  in-memory exporter without the once-only global). W3C `traceparent` is
  persisted on the run and the outbox row because the outbox crosses a process
  boundary with no live span; the worker continues it as `task.execute`. Chain:
  `run.trigger` → `task.dispatch` → `task.execute`.
- **Dashboards as code + compose profile.** `deploy/observability/` holds the
  Prometheus scrape config, the OTel collector pipeline, the Grafana datasource
  and a read-only provisioned dashboard. `docker compose --profile observability
  up` adds Prometheus (9090), Grafana (3000) and an OTel collector (4318/4317).
  The `docker` CI job asserts all four FlowForge targets are UP, the dashboard is
  provisioned and the collector accepts an OTLP export.
- **Load test + benchmark.** `loadtests/locustfile.py` drives the whole pipeline
  (trigger → poll to terminal), records `trigger` and `run end-to-end` as custom
  events, and sends a unique `Idempotency-Key` per attempt. `manage.py
  loadtest_user` creates the fixture account. The `loadtest` CI job runs a fixed
  10-user/60-second shape and uploads the raw CSVs.
  - Measured: 160 runs / 640 task runs, zero failures, 2.54 runs/s, end-to-end
    median 3 500 ms (p95 4 100 ms) at `SCHEDULER_INTERVAL=0.25`.
  - At the default 5 s interval the same test gives a 20 000 ms median, which is
    `4 dependency levels × 5 s` — the cadence, not the engine.
  - [`BENCHMARKS.md`](BENCHMARKS.md) states the hardware, the configuration and
    what the numbers are not.
- **Profiled bottleneck fixed.** `_record_task_run_event` fetched the task run's
  `organization_id` with a separate `SELECT` whenever the caller did not pass it,
  and no caller ever did — although all five already held the row. On a 14-task
  diamond that is 13 wasted round trips per run, every pass.
  - 5.00 → 4.00 queries per transition (−20%); 1 672 → 1 542 queries over 140
    scheduler passes (−7.8%).
  - Reproduce with `scripts/profile_scheduler.py --micro`.
- `THROTTLE_AUTH_RATE` / `THROTTLE_TRIGGER_RATE` / `THROTTLE_READ_RATE` and
  `SCHEDULER_INTERVAL` are now configurable; the shipped defaults are unchanged
  and asserted by a test.

### Phase 10 — SDK, CLI and documentation

- **`flowforge-client`** (`sdk/`): a separate distribution with **no runtime
  dependencies**, because the server package drags in Django and a client library
  must not constrain its host's resolution. It imports nothing from `core` or
  `api`.
  - Frozen dataclasses (`Run`, `TaskRun`, `Workflow`, `Event`, `Page`) parsed by
    explicit `from_payload` classmethods, so a field the server adds is ignored
    and a field it removes fails at parse time rather than three layers down.
  - Typed errors parsed from the API's `error.code`: `NotFoundError`,
    `ConflictError`, `ThrottledError` (with `retry_after`), `ValidationError`,
    `AuthenticationError`, `ForbiddenError`, `ServerError`. A 5xx never becomes a
    4xx type even when the body claims a 4xx code.
  - `trigger(..., idempotency_key=...)` refuses to retry without a key, because a
    retried POST would start a second run; with one, a retry replays.
  - `wait()` polls with a doubling interval and raises `TimeoutError` naming the
    last state. `wait_for_success()` additionally raises when the run ended badly.
  - Retries are 429/5xx only, with exponential backoff and jitter, honouring
    `Retry-After`.
  - The transport refuses any scheme but `http`/`https`; `urlopen` also accepts
    `file:`, and a caller-supplied base URL that could point at a local path is a
    credential-disclosure bug.
- **`flowforge` CLI**: one subcommand per client call, so it cannot do what the
  SDK cannot. `FLOWFORGE_URL`/`FLOWFORGE_TOKEN` first, flags second. Exit codes
  0/1/2, and `trigger` exits 1 when the run did not succeed.
- **Examples** (`examples/`): publish-and-run, watch-the-event-log,
  requeue-failed (dry-run by default), and a CLI walkthrough asserting an exit
  code at every step.
- **Documentation**: `docs/sdk/index.md` (guide), `docs/cli.md` (reference),
  `docs/api/reference.md` (every endpoint with its request and response body).
  The API overview's endpoint table now covers the whole surface.
- **CI gains an `sdk` job**: its own lock file, ruff, ruff format, mypy, pytest,
  an assertion that the runtime dependency list is still empty, and a wheel build
  that checks the `flowforge` console script is declared.

### Phase 11 — Production hardening (in progress)

- **Security review.** gitleaks over the full git history with push protection;
  Trivy image scanning with a CRITICAL gate (the HIGH residual is published, not
  hidden); a STRIDE threat model at `docs/security/threat-model.md`; ADR-014 and
  the tenant-isolation suite re-reviewed. `SECURITY.md` overhauled with a private
  disclosure process and release-image verification.
- **Migration safety.** `tests/unit/test_migrations.py` proves forward-from-empty
  and the `v0.9.0 → HEAD` upgrade with data on PostgreSQL; every migration is
  classified in `docs/deployment/migrations.md` (no table rewrite exists).
- **Kubernetes.** Helm chart + rendered manifests; `k8s` CI job runs `helm lint`
  and `kubeconform -strict` and fails on manifest drift; a kind smoke test is
  available on demand.
- **Backup and restore.** `docs/deployment/backup-restore.md` plus a CI drill
  that dumps, wipes, restores, checks row counts, and runs a workflow end to end.
- **High availability.** `docs/deployment/ha.md` — the four processes scale out
  by construction; PostgreSQL/RabbitMQ/Redis are the failure domains.
- **End-to-end suite.** `tests/e2e/` (REST, SDK, CLI) runs in the `docker` job.
- **Supply chain.** CycloneDX SBOM, keyless cosign signing, build provenance,
  digest-pinned base image, and `release.yml` to attach the SBOM to a release.
- **Docs site.** `pages.yml` publishes the MkDocs site to GitHub Pages; the README
  and PROGRESS record were corrected (Phase 6 status, "not implemented" rows, and
  the "no benchmarks" claim were all stale).

## Verification

### In CI (authoritative)

Phase 8 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39921080486
(v0.9.0 — 645 unit tests on PostgreSQL + 29 integration tests green; `lint`
includes `bandit` and `pip-audit`; the docker job drove the cron and saga e2e
through the real stack)

v0.9.1 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39924920840
(661 unit tests on PostgreSQL + 29 integration tests green; `lint`, `schema`,
`docs` and `docker` green)

Phase 9 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39962017430
(681 unit tests on PostgreSQL + 29 integration tests green; `lint`, `schema`,
`docs` and `docker` green). The `docker` job additionally asserted that all four
FlowForge Prometheus targets were UP (`flowforge targets up: 4`), that the
scheduler/publisher/worker metrics endpoints answered on 9101/9102/9103, that the
Grafana dashboard was provisioned under its uid, and that the OTel collector
accepted an OTLP/HTTP export. The `loadtest` job ran the benchmark and uploaded
its raw output, which is what [`BENCHMARKS.md`](BENCHMARKS.md) is built from.

### Locally (Windows, Python 3.13.14)

| Check | Result |
|-------|--------|
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS |
| `uv run mypy flowforge core api --ignore-missing-imports` | PASS (73 files) |
| `uv run pytest tests/unit` | 681 passed (SQLite) |
| `manage.py makemigrations --check --dry-run` | No changes detected |
| `uv run mkdocs build --strict` | PASS |
| `uv run bandit -r core api flowforge -c pyproject.toml` | 0 issues |
| `uv run pip-audit` | No known vulnerabilities |

`make test-pg` is the local PostgreSQL run and needs Docker. CI already runs the
full unit suite on PostgreSQL, so a machine without Docker is not unverified —
it is the same suite against the same engine in CI.

The load test and the compose observability profile cannot run locally (no
Docker daemon, owner decision 2026-10-19); both are verified in CI, and the
benchmark numbers in [`BENCHMARKS.md`](BENCHMARKS.md) come from that run.

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

11. **Base image is pinned by digest — resolved in Phase 11.** `python:3.13-slim`
    is referenced by digest, so it no longer drifts. The trade is deliberate and
    documented: the image stops receiving updates by itself, and Dependabot opens
    a PR when the digest moves; until that merges, the image is exactly the one
    reviewed.
12. **`tests/e2e/` now exists — resolved in Phase 11.** The end-to-end suite
    drives the REST API, the SDK and the CLI against the Compose stack and runs in
    the `docker` CI job. It is still CI-only for the same reason as everything
    container-level (no local Docker daemon), but the coverage is a proper suite
    rather than inline job steps.
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

### Observability

20. **No alerting rules.** The compose profile ships a dashboard, not an
    Alertmanager configuration. Alert thresholds depend on what a deployment
    considers normal, which is an operator decision.
21. **Trace export needs one environment variable.** `OTEL_EXPORTER_OTLP_ENDPOINT`
    is empty by default; an endpoint that is always set makes the plain stack log
    a failed export every batch interval against a collector that is not running.
22. **The collector exports to its debug sink.** Point the `otlp` exporter in
    `deploy/observability/otel-collector-config.yaml` at a real backend to keep
    spans.
23. **Histogram buckets are fixed in code.** A quantile between two bucket
    boundaries is interpolated by Prometheus, not measured.
24. **The load-test numbers come from CI hardware.** Load generator, database,
    broker and workers share one 4-vCPU VM, and the web process is Django's
    `runserver`. The figures in [`BENCHMARKS.md`](BENCHMARKS.md) are a floor.
25. **`load_definition` is still re-queried on every scheduler pass.** It is ~19%
    of `schedule_once` in a profile. Caching it would remove a deliberate
    re-validation of the stored rows, so it was left alone; this is a known,
    measured cost rather than an oversight.
26. **`SCHEDULER_INTERVAL` sets a floor on run latency.** A run cannot finish
    faster than `dependency_depth × interval`. The default 5 s means a four-task
    diamond takes ~20 s even when the engine is idle.

### Client

27. **`flowforge-client` is not published to PyPI.** The package builds and its
    wheel is checked in CI, but publishing is a release decision that has not been
    taken. Install it from the repository (`pip install ./sdk`) until it is.
28. **The SDK has no async client.** The interesting latency is the pipeline's,
    not the HTTP call's; a second implementation would have to be kept in step for
    no measured benefit.
29. **The SDK does not validate definitions locally.** The server owns the
    schema; validating twice would create two sources of truth that can disagree.
30. **The client has no cancellation call**, because the API has no cancellation
    endpoint. `request_cancellation` exists in the service layer and is not
    exposed over HTTP yet.
31. **The CLI has no `publish` subcommand.** A workflow definition is a structured
    document, not a flag; publishing from a shell heredoc is worse than from
    Python or the API.

### Production hardening (Phase 11)

32. **High availability is guidance, not a tested topology.** `docs/deployment/ha.md`
    proves the four application processes are safe to run as multiple replicas (the
    database is the mutex), but a genuinely multi-replica deployment, PostgreSQL
    streaming-replication failover, and a multi-node RabbitMQ cluster are **not**
    stood up in CI. Validate them on your own infrastructure.
33. **The restore drill proves the procedure, not production volume.** It runs
    against a database with a handful of rows. Dump/restore *correctness* is
    proven; runtime against terabytes is not measured.
34. **The release SBOM-attach automation is not yet exercised.** `release.yml`
    attaches the CycloneDX SBOM to a published release, but it has not run end to
    end because no release has been cut since it was added. Its steps (tag→commit
    resolution, run lookup, artifact download) were each validated against the
    repository directly.
35. **GitHub Pages needs a one-time enable.** `pages.yml` builds and deploys the
    docs site, but the repository's Pages source must be set to "GitHub Actions"
    once (Settings → Pages) before the first deploy succeeds.
36. **Kubernetes is validated by lint, not by a cluster in the required gate.**
    `helm lint` and `kubeconform -strict` run on every push; the kind end-to-end
    smoke test (`scripts/kind-e2e.sh`, `k8s-e2e.yml`) is manual-dispatch and is
    intentionally not in the required gate.

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
| structlog + django-structlog | 26.1.0 / 10.1.0 | Structured logging; JSON renderer |
| prometheus-client | 0.26.0 | Metrics registry and exposition |
| opentelemetry-api / sdk | 1.45.1 | Tracing; module-local provider |
| opentelemetry-exporter-otlp-proto-http | 1.45.1 | Span export when an endpoint is set |
| locust | 2.47.0 | Load tests (dev extra only; not in the image) |
| Python | 3.13 in CI/Docker; `>= 3.11` | 3.14 deferred (limitation 18) |
| bandit + pip-audit | dev extra | Required `lint` checks |
| flowforge-client | 0.11.0, `sdk/` | Separate distribution, **zero runtime dependencies** (limitation 27) |

## Blockers

None.

## Next step

Phase 11 — Production Hardening — is implemented on `feat/phase11-hardening` and
covers the full scope in [`ROADMAP.md`](ROADMAP.md): security review, migration
safety, Kubernetes, backup/restore, high availability, an end-to-end suite, and
release preparation (SBOM, signing, docs site). Remaining before `v1.0.0`:

1. Open the Phase 11 pull request and get every CI job green (the `docker` job's
   new restore drill and `tests/e2e/` are the steps that exercise the most new
   logic).
2. Merge on green CI (automatic per the release policy).
3. Cut `v1.0.0` — **which needs the owner's explicit approval before it is
   tagged.** The draft release notes and the release checklist are in
   `docs/development/`; `release.yml` attaches the SBOM once the release is
   published, and `pages.yml` publishes the docs site from `main`.

Every release tag other than `v1.0.0` is automatic on green CI.

## Ordering note

A CI URL is added only after the run is green. Each URL above is the run that
produced the cited counts; the merge commit is the one that follows it.
