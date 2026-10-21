# Progress

## Current Phase

**Phase 5 — Distributed Workers: COMPLETE (v0.6.0).** Verification gaps are being
closed in this PR (`fix/phase5-verification-gaps`), which will be tagged **v0.6.1**
on merge. Phase 6 — Reliability — is next.

## Resume point

If this session is resumed cold, read this section first.

| Question | Answer |
|----------|--------|
| Where is the work | `fix/phase5-verification-gaps`; on merge it becomes `v0.6.1`. `main` is the integration branch. |
| What is released | `v0.1.0` … `v0.6.0`, plus `v0.5.1` (Phase 4 gap). `v0.6.1` (Phase 5 gaps) is the target of this PR. |
| Framework | **Django 5.2 LTS** (owner decision, [ADR-010](../adr/ADR-010-django-5-2-lts.md)). Python stays at 3.13. |
| Message broker | **RabbitMQ via pika**, no Celery ([ADR-011](../adr/ADR-011-custom-scheduler-and-transactional-outbox.md)). `RABBITMQ_URL` is the one broker. |
| What is next | Merge the v0.6.1 PR, tag `v0.6.1`, then Phase 6 — Reliability (retries, dead-letter, backoff). |
| What blocks Phase 6 | **Nothing.** |
| What else is open | Nothing pending an owner decision. |

## Status

| Item | State |
|------|-------|
| Branch | `fix/phase5-verification-gaps` → will be tagged `v0.6.1` on merge |
| Phase 0 PR | #1 — **MERGED** as `97c17bf` |
| Stack upgrade | **MERGED** as `525e919` (Django 5.2 LTS, DRF 3.17, Actions re-pinned) |
| Phase 1 | **MERGED** as `869720f`, tagged `v0.2.0` |
| Phase 2 | **MERGED** as `d31abf3`, tagged `v0.3.0` |
| Phase 3 | **MERGED** as `405cb3d`, tagged `v0.4.0` |
| Phase 4 | **MERGED** as `a68ac2a` (PR #25), tagged `v0.5.0`; gap closed by PR #27 (`v0.5.1`) |
| Phase 5 | **MERGED** as `6b0564f` (PR #28), tagged `v0.6.0`; verification gaps close in this PR (target `v0.6.1`) |
| CI | Green on `main`: `lint`, `test`, `schema`, `docs`, `docker` (Phase 5 run `39506564517`) |
| Docker | **Verified in CI** (build + compose up + health probes + e2e). Accepted by owner decision 2026-10-19 — no local check is required |
| Tags | `v0.1.0` … `v0.6.0` annotated; `v0.5.1` is a follow-up release. `v0.6.1` will be tagged on merge of this PR. |
| GitHub Releases | Published for `v0.1.0` … `v0.6.0` and `v0.5.1`. `v0.6.1` pending this PR's merge. |
| Branch protection | Active on `main`: `lint`, `test`, `docker` required, strict, no force push, 0 approving reviews, **admins cannot bypass** (`enforce_admins`) |
| Django | **5.2 LTS** (ADR-010) |
| Python | 3.13 (3.14 deferred — see limitations) |
| Phase 4 | Complete — scheduler, outbox, publisher, lease reaper; no Celery. Publisher verified against a real broker. |
| Phase 5 | Complete — worker with manual ack, prefetch, trusted handlers, lease+fencing, heartbeat, graceful shutdown. The e2e sample reaches `SUCCEEDED` in the compose CI job, in-process and via the REST API. |

## Completed

1. Isolated Git repository inside `FlowForge/`.
2. Django 5.1 project, package `flowforge`.
3. PostgreSQL, Redis, RabbitMQ wiring via `django-environ`.
4. Settings package: `flowforge/settings/{base,dev,test,prod}.py`.
5. `SECRET_KEY` mandatory when `DEBUG` is off; prod rejects short keys and
   placeholders and requires non-empty `ALLOWED_HOSTS`.
6. `core` app with `/health/live/` and `/health/ready/`.
7. Readiness probes PostgreSQL (`SELECT 1`), Redis (cache round-trip) and
   RabbitMQ (a real AMQP connection open/close, not a TCP connect), returning 503
   with a per-dependency payload.
8. Scheduler and publisher management commands (ADR-011); Celery removed in Phase 4.
9. `api/v1/` REST API (Phase 3): workflows, runs, token/JWT auth, cursor pagination.
10. Ruff (lint + format), mypy strict, pytest, pytest-cov, pre-commit.
11. `uv.lock` committed; the Docker image and CI both install from it.
12. CI: lock check, lint, format, mypy, migration check, unit tests against
    PostgreSQL + Redis, integration tests against PostgreSQL + RabbitMQ, and a
    compose smoke test that brings up all eight services, probes both health
    endpoints, and drives the sample workflow to `SUCCEEDED` in-process and via
    the REST API.
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

### Phase 1 — Domain Modeling

25. `core/domain/` is a pure-Python package with **no Django import**, enforced
    two ways: an AST walk over every module and a subprocess re-import with
    `django` blocked on `sys.meta_path`. The scanner is itself tested against
    deliberately bad source.
26. `WorkflowRunState` (pending, running, succeeded, failed, cancelled) and
    `TaskRunState` (pending, ready, running, succeeded, failed, retrying,
    cancelled) as `StrEnum`.
27. `StateMachine[T]` with `Transition` dataclasses, validated at import time:
    duplicate edges, duplicate names, edges out of a terminal state, dead ends,
    and states from the wrong enum all raise `ValueError`.
28. `WORKFLOW_RUN_MACHINE` (5 edges, 3 terminal) and `TASK_RUN_MACHINE` (9 edges,
    2 terminal). `FAILED` is deliberately non-terminal for `TaskRun` so a retry
    reuses the row instead of breaking `(workflow_run, task)` uniqueness.
29. Frozen dataclass entities with `__post_init__` invariants, one test per rule.
30. `derive_workflow_run_state` with documented precedence
    (cancelled > failed > all-succeeded > running > pending) and
    `initial_task_run_state` for DAG eligibility.
31. Persistence models with UUID PKs, six `CHECK` constraints and five named
    indexes. Constraints are proven by writing through `QuerySet.update()` so
    every Python-level guard is bypassed (`tests/unit/test_constraints.py`).
32. `core/repositories/transitions.py`: atomic conditional `UPDATE` returning
    whether the transition won; `claim_task_run` increments the fencing token in
    the same statement and returns it; `finish_task_run` requires a matching
    token, so a resurrected worker's write matches zero rows.
33. Hypothesis `RuleBasedStateMachine` property tests plus exhaustive table
    assertions, including a regression test for the `StrEnum` equality trap
    (`TaskRunState.CANCELLED == WorkflowRunState.CANCELLED` is `True`).
34. Concurrency tests on real PostgreSQL: 16 threads released by a
    `threading.Barrier`, no sleeps. Five tests: exactly one claim wins, the
    winner is the recorded worker, exactly one run start wins, one writer per
    token, and a stale token loses under concurrency. CI now runs these with
    `REQUIRE_POSTGRES=1`, so they can no longer skip silently.
35. `docs/architecture/data-model.md` and `docs/architecture/execution-model.md`
    — the latter with Mermaid state diagrams for both machines and a
    fencing-token sequence diagram.

### Phase 2 — Workflow Definition Engine

36. `TaskDefinition` and `WorkflowDefinition` as frozen dataclasses in
    `core/domain/definition.py`, with no framework import. `config` is normalised
    into a `MappingProxyType` so a caller's dict cannot mutate a validated
    definition.
37. Config is validated to be JSON-shaped **at construction**. The freeze was
    only reachable through `__hash__` and `fingerprint()`, so a non-JSON value
    passed construction and surfaced later as an unattributed `TypeError` — the
    same laziness class as the unforced `graph` below.
38. `DependencyGraph` validates duplicate keys, unknown dependencies and cycles
    in its constructor. Cycle detection is **iterative** (an explicit stack of
    `(node, next_child_index)` frames), so a 3000-deep chain whose back-edge sits
    at the far end is found. A recursive walker overflows at roughly 1000 frames.
39. `topological_order()`, `levels()`, `ready_tasks()`, `is_ready()`,
    `transitive_dependencies_of()`, `transitive_dependents_of()` and the container
    protocol, all deterministic — ties broken by ascending task key.
40. `core/domain/schema.py`: a Draft 2020-12 schema for payload shape, reported
    with JSONPaths. JSON Schema deliberately stops at types and required fields;
    duplicate keys, dangling references and cycles are domain errors, because the
    standard cannot express them.
41. A payload that is **both** malformed and cyclic produces the shape error —
    the cheaper fix first. Both branches are tested.
42. `WorkflowDefinition.fingerprint()` over name, version, description and every
    task's content. **`description` is included**: omitting it meant a definition
    whose only change was its description silently returned the stored row, with
    the caller believing text the database did not hold. Regression-guarded, and
    verified to fail without the fix.
43. `core/repositories/definitions.py`: `publish_definition` forces the graph
    **before** the transaction opens, writes the workflow and its tasks in one
    transaction, and is idempotent by fingerprint. Differing content under an
    existing `(name, version)` raises `DefinitionConflictError`.
44. `load_definition` round-trips losslessly and **re-validates on the way out**,
    so a manual `UPDATE` or a data-loss migration is caught at read time rather
    than at schedule time inside a transaction. It refuses rows predating
    `core.0002` by name instead of guessing a `task_type`.
45. Migration `core/0002_definition_payload.py` adding `description`,
    `task_type`, `depends_on` and `config` as columns, so loading is lossless
    rather than reconstructing `task_type` from the task key.
46. Hypothesis property tests: `@st.composite dags()` builds graphs acyclic by
    construction (task `t{i}` may only depend on `t{<i}`), while
    `arbitrary_graphs()` covers cycle-soundness — a rejected graph's reported
    cycle is checked edge by edge against the declared edges, and an acyclic
    graph must never be rejected. Invariants include permutation, levels
    partitioning, readiness safety, drainability, determinism across declaration
    order, and closure correctness.
47. Publish-race tests on real PostgreSQL: 16 threads, one `threading.Barrier`,
    no sleeps. Two races — identical content and conflicting content. `OperationalError`
    (PostgreSQL's `deadlock detected`) is deliberately **not** an accepted
    outcome, so a deadlock fails the test.
48. `ready_tasks()` returning already-completed tasks was found and fixed. A
    scheduler acting on that would execute a task twice.
49. `flowforge.settings.test` now ignores a `DATABASE_URL` that exists only in
    `.env`. `base` merges the file into `os.environ`, which made a file value
    indistinguishable from a caller-supplied one and broke the suite's own "no
    external services" contract on any machine with a checked-out `.env`. CI is
    unaffected because it sets the variable at job level.
50. `tests/integration/conftest.py` now holds the barrier, per-thread connection
    cleanup and the no-stragglers check, shared by the transition race and the
    publish race, with a second entry point for races whose losers are expected
    to raise.
51. `docs/architecture/definitions.md`: lifecycle diagram, why definitions are
    value objects, why cycle detection is iterative, the shape-vs-graph boundary,
    and an honest account of what the publish race does **not** guarantee.

### Phase 4 — Scheduler

52. ADR-011 accepted (owner decision 2026-10-20): Celery removed, replaced by a
    custom scheduler, a transactional outbox, a publisher, and (Phase 5) a worker.
    The worker claims, not the scheduler.
53. `core/repositories/scheduler.py`: `advance_workflow_run` promotes
    dependency-satisfied tasks, starts the run and writes one outbox row per
    dispatch generation in one transaction; `enqueue_ready_tasks` is idempotent
    per generation; `schedule_once` reaps expired leases then advances every
    active run.
54. `core/models.OutboxMessage` + migration `core.0004_outbox`: the transactional
    outbox, with a unique `(task_run, fencing_token_snapshot)` constraint and the
    partial index `idx_outbox_pending`.
55. `create_dispatch` pre-checks existence before inserting. Catching the UNIQUE
    violation alone aborted the open SQLite transaction, and the next query then
    raised `TransactionManagementError`; the pre-check plus a savepoint fixes it.
56. `core/repositories/transitions.py`: the lease reaper (`reclaim_task_run`,
    `reap_expired_leases`), a conditional `UPDATE ... WHERE state='running' AND
    lease_expires_at < now` that bumps the fencing token. The `reap_lease` edge is
    reachable only through the reaper, never the generic transition API.
57. `core/broker.py` + `core/broker_rabbit.py` + `manage.py publisher`: the
    publisher drains the outbox with `FOR UPDATE SKIP LOCKED`, publishes with
    publisher confirms, and marks a row delivered only after the broker acks.
58. `manage.py scheduler` and `manage.py publisher` replace celery beat/worker in
    compose; the `worker` service is renamed `publisher`.
59. Celery, kombu, billiard and vine removed; `CELERY_*` settings replaced by
    `RABBITMQ_URL`; ADR-007 superseded.
60. Tests: 373 unit and 11 PostgreSQL concurrency tests (5 transition races, 2
    reaper races, 3 publish races, 1 publisher SKIP LOCKED race).
61. `docs/architecture/execution-model.md` and `data-model.md` updated for the
    reaper edge, the outbox table, and the scheduler/publisher processes.
62. **v0.5.1 — publisher verified against a real broker.** CI runs a
    `rabbitmq:3.13-alpine` service and the integration suite against it with
    `REQUIRE_RABBITMQ=1`: confirms + arrival, unroutable leaves the row pending,
    outage then recovery, crash-window redelivery, and two publishers never
    duplicating a row. The readiness probe now opens a real AMQP connection
    instead of a TCP connect, and `mandatory=True` keeps an unroutable dispatch
    pending instead of dropping it.

### Phase 5 — Distributed Workers

63. `core/worker/registry.py` + `core/worker/builtin.py`: a trusted, in-process
    handler registry. A dispatch names a `task_type`; nothing in it is imported or
    executed as code, and an unknown type fails the task.
64. `core/worker/consumer.py`: manual ack (`auto_ack=False`), prefetch, a lease
    heartbeat thread, a graceful stop, and a `process` method separated from pika
    so the decisions are unit-testable.
65. `core/repositories/transitions.py`: `renew_lease`, the heartbeat's conditional
    `UPDATE` that matches only while the token still owns the row.
66. `manage.py worker`: consumes dispatches; SIGTERM/SIGINT stop after the in-flight
    message is acked.
67. `advance_workflow_run` now completes the workflow run when its tasks resolve
    (`derive_workflow_run_state`), so a correct run no longer stays `RUNNING`.
68. `manage.py sample_run` + a `worker` compose service: the end-to-end sample
    (echo → add → noop) the docker CI job drives to `SUCCEEDED`.
69. Tests: unit (success, handler failure, unknown type, duplicate delivery, finish
    failure, stale finish, malformed payload, heartbeat, killed-worker stale
    rejection, registry, run completion); integration (end-to-end consume,
    duplicate delivery, lost ack redelivery, graceful shutdown).
70. `docs/architecture/execution-model.md` records the worker's contract and what it
    does not claim.

## Verification

### In CI (authoritative)

Phase 5 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39506564517
(v0.6.0 — worker; 389 unit + 20 integration green; docker e2e reached `SUCCEEDED`)

Phase 2 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39252710339

| Job | Result |
|-----|--------|
| lint (ruff, ruff format, mypy) | PASS |
| test — unit (270 passed, 98.12% coverage, gate 80%) | PASS |
| test — integration (8 PostgreSQL concurrency tests, `REQUIRE_POSTGRES=1`) | PASS |
| test — Codecov upload (`Upload queued for processing complete`) | PASS |
| docker (build, compose up, health probes) | PASS |

The integration step ran all eight tests for real — the five transition races
from Phase 1 and the three new publish races:

```
tests/integration/test_concurrent_publish.py::test_publishing_identical_content_concurrently_yields_one_workflow PASSED
tests/integration/test_concurrent_publish.py::test_publishing_different_content_concurrently_holds_one_definition PASSED
tests/integration/test_concurrent_publish.py::test_definition_exists_is_true_for_the_different_content_race PASSED
============================== 8 passed in 2.52s ===============================
```

Phase 1 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39226452632
| Job | Result |
|-----|--------|
| lint (ruff, ruff format, mypy) | PASS |
| test — unit (143 passed, 97.55% coverage, gate 80%) | PASS |
| test — integration (5 PostgreSQL concurrency tests, `REQUIRE_POSTGRES=1`) | PASS |
| test — Codecov upload (`Upload queued for processing complete`) | PASS |
| docker (build, compose up, health probes) | PASS |

Note: the Codecov step was skipped on the Phase 0 run because no `CODECOV_TOKEN`
existed. The secret was added on 2026-10-19 and the upload now succeeds.

Phase 0 run: https://github.com/Abolfazlrwm/flowforge/actions/runs/39211983697

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

1. **Handler return values are not persisted.** The worker records the task
   outcome (SUCCEEDED/FAILED) durably, but a handler's return value is discarded.
   Result storage belongs to Phase 6; today the outcome is the result.
2. **The publisher's broker path is verified against a real broker in CI** (v0.5.1).
   `tests/integration/test_rabbit_publisher.py` runs against a RabbitMQ service
   container with `REQUIRE_RABBITMQ=1`: confirms + arrival, mandatory routing,
   outage then recovery, crash-window redelivery, and two publishers without
   duplicates. No remaining owner-side verification is required.
3. **Readiness checks are mostly real but not exhaustive.** RabbitMQ is checked
   with a real AMQP connection open/close (not a TCP connect), Redis with a cache
   write (which would also pass against a different cache backend), and PostgreSQL
   with `SELECT 1`. None asserts the broker's vhost/permissions beyond a successful
   handshake.
4. **`make demo` does not run a workflow yet.** It brings up the stack and probes
   both health endpoints. A real end-to-end run is exercised by `manage.py
   sample_run` (in-process) and by the REST API in the compose CI job, not by
   `make demo`.

### Tooling

5. **Base image pinned by tag, not digest.** `python:3.13-slim` can drift.
6. **No dedicated `tests/e2e/` directory.** End-to-end coverage lives in the
   compose CI job: `manage.py sample_run` drives a workflow in-process to
   `SUCCEEDED`, and a separate step drives the same flow through the REST API
   (auth → publish → trigger → poll). The SIGTERM subprocess test
   (`tests/integration/test_worker_sigterm.py`) covers graceful shutdown against
   real PostgreSQL + RabbitMQ. These run only in CI (no local Docker/PostgreSQL).
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
13. **redis-py stays below 6 for the Redis cache backend.** The Celery-driven pin
    is gone (ADR-011); the remaining reason is cache-backend compatibility, which
    is verified separately before a major bump.
14. **Celery is gone.** Removed in Phase 4 (ADR-011); the Celery Dependabot
    ignore was removed with it.
15. **Docker is verified in CI only, and that is now sufficient by owner
    decision.** The daemon is offline on the maintainer's machine, so
    `docker compose up` has never run there. On 2026-10-19 the owner withdrew the
    earlier "do not tag until Docker is verified on my machine" rule and accepted
    the CI `docker` job (build + compose up + both health probes) as the evidence.
    Recorded in [DECISIONS.md](DECISIONS.md).
16. **The Phase 2 publish-race tests have not run on the developer's machine.**
    A local PostgreSQL is listening on `localhost:5432` but rejects the
    `flowforge` credentials in `.env` (`password authentication failed`), so the
    three new concurrency tests skip locally — the same CI-only coverage Phase 1's
    transition races have. The developer's own credentials were not changed to
    force a local run: the owner's database is not theirs to modify. CI supplies
    `POSTGRES_PASSWORD: flowforge`, so the tests run for real there.
17. **`publish_definition` does not serialise concurrent first-publishes.**
    `select_for_update()` cannot lock a row that does not exist, so two callers
    publishing the same `(name, version)` for the first time both see "not there"
    and both insert. The unique constraint picks the winner and the loser's
    transaction rolls back, which leaves the database correct — but the loser sees
    an `IntegrityError` rather than a friendly conflict, and it is the caller's
    job to retry. Serialising would need a whole-range lock or a retry loop;
    neither was added. Documented in `docs/architecture/definitions.md`.
18. **`docs/architecture/definitions.md` is the only new architecture document,
    and its Mermaid diagram is not rendered in CI.** There is no Markdown link
    checker and no diagram render step in the pipeline; the diagram is verified
    by reading, not by a build.
19. **The MkDocs site is now built in CI, but a Mermaid syntax error is still
    invisible.** Owner decision on 2026-10-19 added a `docs` job running
    `mkdocs build --strict` with `mkdocs-material` and `pymdownx.superfences`, so
    a broken internal link, a nav entry with no target, or an unparseable page
    fails the build. Resolving limitation 19 required creating `docs/index.md`
    (the nav pointed at a file that did not exist) and fixing a link to the
    `docs/adr/` directory. **What the build still cannot catch:** a diagram whose
    Mermaid syntax is wrong renders as an error box in the browser and passes CI.
    Deploying to GitHub Pages is deferred to Phase 10.

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

None. Phase 6 is unblocked.

## Next step

Phase 5 is complete (`v0.6.0`) and its verification gaps close in this PR (target
`v0.6.1`). Proceed to **Phase 6 — Reliability (v0.7.0)**: retry policies,
exponential backoff with jitter, dead-letter handling, and persisting handler
results. The `retrying` state and `schedule_retry` edge already exist; Phase 6 is
the policy that drives them and the DLQ.

Phase 5 left two things Phase 6 will feel: **a failed task does not retry** (the
worker marks it FAILED and the run FAILED; nothing schedules a retry or a
dead-letter route yet), and **handler return values are not persisted** (only the
task outcome).

## Ordering note

The CI URL for Phase 2 was added in a second commit inside the same PR, once the
run was actually green (`39252710339`). Committing a CI URL before the run exists
would be a claim rather than a verification, and the Codecov upload — disabled in
Phase 0 by exactly that kind of assumption — is the precedent for why the URL is
not written in advance.
