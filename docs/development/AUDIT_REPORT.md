# Phase 0 — Independent Audit Report (closeout)

**Audit started:** 2026-10-18
**Closeout:** 2026-10-19
**Branch:** `chore/phase0-audit-fixes`
**PR:** #1 — open, **not merged**
**v0.1.0 tag:** **not created** — blocked on Docker verification by the owner

## Commit map

| Commit | Purpose |
|--------|---------|
| `74e4a4c` | Initial Phase 0 delivery under review |
| `4e86b54` | First audit fix round (18 defects). Also contained an **unapproved** rename to "TaskForge". |
| `db3bc71` | First audit report (superseded by this document) |
| `1352e22` | Revert the rename; split settings into `base/dev/test/prod` |
| `bc38824` | Behavioural tests for health probes and security settings |
| `6b4cfe2` | `uv.lock`, CI installs from the lock, actions pinned to SHAs |
| `7fd07aa` | Documentation closeout |
| `0a27d5f` | Docker build: install from `uv.lock`, copy `README.md` |
| `82c4ccc` | CI: bring up the compose stack and probe both health endpoints |
| `e9a2417` | ADR-009: owner-delegated project name evaluation |
| `da10a23` | README rewrite and a Makefile with verifiable targets |
| `3b6dc37` | Record the CI verification of the Docker stack |
| `a0571bf` | One-shot `migrate` gate; shared compose env anchor (defects #25, #26) |
| `d654298` | `.gitattributes` forcing LF (defect #27) |

## Step 1 — Repository safety

| Check | Result | Evidence |
|-------|--------|----------|
| Git root | Project directory itself, not a parent repo | `git rev-parse --show-toplevel` → `C:/Users/CMG/Desktop/Git hub/FlowForge` |
| Remote | `https://github.com/Abolfazlrwm/flowforge.git` | `git remote -v` |
| Visibility | **PUBLIC** | `gh repo view --json visibility` → `"PUBLIC"` |
| Branches at audit start | `main` only | `git branch -a` |
| History | 1 commit at audit start | `git log --oneline -20` |
| Unrelated files committed | None | Every tracked path belongs to the project |
| Deleted files in history | None | `git log --all --diff-filter=D --summary` → empty |

## Step 2 — Verification results

| Command | At audit start | Now |
|---------|----------------|-----|
| `ruff check .` | PASS | PASS |
| `ruff format --check .` | **FAIL** — 3 files | PASS (42 files) |
| `mypy flowforge core api` | PASS | PASS (17 files) |
| `pytest tests/unit --cov` | **FAIL** — 0% coverage, gate 80% | PASS — 13 tests, 100% |
| `manage.py check --deploy` | 5 warnings | **0 issues** (prod settings, strong key) |
| `manage.py makemigrations --check --dry-run` | PASS (no models) | PASS |
| `docker compose config` | Valid | Valid |
| `docker build` / `compose up` | **UNVERIFIED** | **UNVERIFIED** |
| `GET /health/live/` on a real server | 200, no dependency checks | 200 `{"status":"alive","service":"flowforge"}` |
| `GET /health/ready/` on a real server | did not exist | 503 `{"status":"degraded","checks":{"postgres":"ok","redis":"error","rabbitmq":"error"}}` |

Deploy check with production settings and a generated key:

```
$ DJANGO_SETTINGS_MODULE=flowforge.settings.prod \
  DJANGO_SECRET_KEY=<64-char random> \
  DJANGO_ALLOWED_HOSTS=example.com \
  uv run python manage.py check --deploy
System check identified no issues (0 silenced).
```

### `check --deploy` warnings at audit start, explained

| Code | Meaning | Why it appeared | Resolution |
|------|---------|-----------------|------------|
| `security.W004` | `SECURE_HSTS_SECONDS` unset | Only read from env, defaulted to 0 | `prod.py` sets 31 536 000 by default |
| `security.W008` | `SECURE_SSL_REDIRECT` not True | Defaulted to False | `prod.py` defaults to True |
| `security.W009` | Weak `SECRET_KEY` | The audit used `audit-secret` | `prod.py` rejects keys < 50 chars and placeholders |
| `security.W012` | `SESSION_COOKIE_SECURE` not True | Setting absent | Derived from `DEBUG`, forced True in prod |
| `security.W016` | `CSRF_COOKIE_SECURE` not True | Setting absent | Derived from `DEBUG`, forced True in prod |

## Step 3 — The 18 defects, with file paths and fixing commits

| # | Defect | File(s) | Fixed in |
|---|--------|---------|----------|
| 1 | `ruff format --check` failed: single quotes and missing blank line after docstring | `flowforge/asgi.py`, `flowforge/wsgi.py`, `manage.py` | `4e86b54` |
| 2 | Coverage `source = ["src"]` pointed at a directory that does not exist, so every run reported 0% and failed the 80% gate | `pyproject.toml` | `4e86b54` |
| 3 | Hatch wheel `packages = ["src/flowforge"]` pointed at a non-existent path, so the package could not be built | `pyproject.toml` | `4e86b54` |
| 4 | `pytest-cov` missing from dev dependencies while `--cov` was used in `addopts` and CI | `pyproject.toml` | `4e86b54` |
| 5 | Health endpoint returned a static `{"status":"ok"}` and never touched the database | `core/views.py`, `core/urls.py`, `flowforge/urls.py` | `4e86b54` (split into liveness/readiness); hardened in `1352e22` |
| 6 | No `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`; deploy check warned W012/W016 | `flowforge/settings.py` → `flowforge/settings/base.py` | `4e86b54`, moved in `1352e22` |
| 7 | No `SECURE_REFERRER_POLICY` | `flowforge/settings.py` → `flowforge/settings/base.py` | `4e86b54`, moved in `1352e22` |
| 8 | Docker image ran as root | `Dockerfile` | `4e86b54` |
| 9 | No `.dockerignore`, so `.git`, `.venv`, `.env` and caches were sent to the daemon | `.dockerignore` | `4e86b54` |
| 10 | CI never checked for missing migrations | `.github/workflows/ci.yml` | `4e86b54` |
| 11 | CI never built the Docker image | `.github/workflows/ci.yml` | `4e86b54` |
| 12 | No Dependabot configuration | `.github/dependabot.yml` | `4e86b54` |
| 13 | `HEALTHCHECK` probed `/health/`, which no longer existed after the probe split | `Dockerfile` | `4e86b54` |
| 14 | `.ruff_cache/` missing from `.gitignore` | `.gitignore` | `4e86b54` |
| 15 | No MkDocs configuration despite documentation claims | `mkdocs.yml` | `4e86b54` |
| 16 | `docs/adr/` existed but was empty; ADRs lived only in a summary file | `docs/adr/` | `4e86b54` |
| 17 | `-n auto` in pytest `addopts` interfered with coverage measurement | `pyproject.toml` | `4e86b54` |
| 18 | Project name conflicted with an npm-reserved name, a PyPI `flow-forge` package, several GitHub projects, a prior venture-funded company (now FlowFuse) and an active commercial product | project-wide | Rename attempt in `4e86b54` **reverted** in `1352e22`; conflicts documented in `docs/development/NAMING_ANALYSIS.md` |

### Additional defects found during closeout

| # | Defect | File(s) | Fixed in |
|---|--------|---------|----------|
| 19 | `[project.scripts] flowforge = "cli.main:main"` referenced a module that does not exist, shipping a broken console script | `pyproject.toml` | `1352e22` |
| 20 | Production image installed `.[dev]`, bundling pytest, ruff and mypy into the runtime image | `Dockerfile` | `1352e22` |
| 21 | No lock file; dependency resolution was unpinned and non-reproducible | `uv.lock` | `6b4cfe2` |
| 22 | GitHub Actions referenced floating tags (`@v4`, `@v5`) rather than immutable commits | `.github/workflows/ci.yml` | `6b4cfe2` |
| 23 | Settings were a single module with no way to express environment differences; `SECRET_KEY` was optional even with `DEBUG=False` | `flowforge/settings/` | `1352e22` |
| 24 | No test exercised readiness failure modes or the security flags | `tests/unit/test_health.py`, `tests/unit/test_settings.py` | `bc38824` |

### Defects found after the first green run

| # | Defect | File(s) | Fixed in |
|---|--------|---------|----------|
| 25 | `web`, `worker` and `scheduler` each ran `migrate` on startup, racing on `CREATE TABLE`. Surfaced as `django.db.utils.ProgrammingError: relation "django_session" already exists` in CI run [39211260120](https://github.com/Abolfazlrwm/flowforge/actions/runs/39211260120); the earlier green run [39210906976](https://github.com/Abolfazlrwm/flowforge/actions/runs/39210906976) passed only by start-order luck | `compose.yaml`, `.github/workflows/ci.yml` | `a0571bf` |
| 26 | `scheduler` and `worker` were missing `DJANGO_ALLOWED_HOSTS` — the three duplicated `environment:` blocks had already drifted | `compose.yaml` | `a0571bf` |
| 27 | `core.autocrlf=true` with no `.gitattributes` wrote CRLF into the working tree; the first shell script committed would fail with `/bin/sh\r: bad interpreter` inside Linux containers | `.gitattributes` | `d654298` |

## Step 4 — Requirement table

Status key: **Done** · **Partial** · **Missing** (deferred by the roadmap, not
a defect) · **Deviation** (implemented differently from the spec).

| # | Requirement | Status | Evidence |
|---|-------------|--------|----------|
| 1 | Isolated repository, not nested in a parent repo | Done | `git rev-parse --show-toplevel` |
| 2 | Django 5.1 project | Done | `pyproject.toml` `django>=5.1,<5.2`; installed 5.1.15 |
| 3 | PostgreSQL as authoritative state | Done | `settings/base.py` `env.db_url("DATABASE_URL")`; `compose.yaml` `postgres:16-alpine` |
| 4 | Redis for cache and result backend | Done | `CACHES` → `RedisCache`; `compose.yaml` `redis:7-alpine` |
| 5 | RabbitMQ as broker | Done | `CELERY_BROKER_URL`; `compose.yaml` `rabbitmq:3.13-alpine` |
| 6 | Custom Scheduler | **Deviation** | Celery beat substitutes for it (`compose.yaml` `scheduler`). ADR-007 records the deviation and defers the decision to Phase 4 |
| 7 | Transactional outbox | **Missing** | Not implemented. `docs/development/ROADMAP.md` Phase 4 |
| 8 | Direct RabbitMQ delivery (publisher confirms, DLX) | **Deviation** | Delivery is delegated to Celery/Kombu; no direct AMQP channel control. ADR-007 |
| 9 | Lease-based execution with fencing tokens | **Missing** | `ROADMAP.md` Phase 5 |
| 10 | DAG workflow definition engine (validation, cycle detection, topo sort) | **Missing** | `ROADMAP.md` Phase 2 |
| 11 | REST API for workflows and executions | **Missing** | `api/v1/urls.py` is an empty placeholder; `ROADMAP.md` Phase 3 |
| 12 | Multi-tenancy, RBAC, tenant isolation | **Missing** | `ROADMAP.md` Phase 8 |
| 13 | Observability: metrics, tracing, dashboards | **Partial** | Structured JSON logging via `structlog` in `settings/base.py`; no metrics or tracing. `ROADMAP.md` Phase 9 |
| 14 | Health endpoint | Done | `/health/live/` and `/health/ready/` in `core/urls.py` |
| 15 | Health endpoint checks database connectivity | Done | `core/views.py` `ReadinessView` runs `SELECT 1` |
| 16 | Separate liveness and readiness probes | Done | `LivenessView` / `ReadinessView`; both verified against a live server |
| 17 | Readiness checks PostgreSQL, Redis **and** RabbitMQ | Done | `checks` payload reports all three; verified 503 with Redis/RabbitMQ down |
| 18 | Settings split into base/dev/test/prod | Done | `flowforge/settings/{base,dev,test,prod}.py` |
| 19 | `DEBUG` defaults to False | Done | `env("DJANGO_DEBUG", (bool, False))`; prod forces False |
| 20 | `ALLOWED_HOSTS` from environment, required in prod | Done | `prod.py` raises `ImproperlyConfigured` when empty |
| 21 | `SECRET_KEY` required when `DEBUG` is off | Done | `settings/base.py` raises `ImproperlyConfigured`; tested in `tests/unit/test_settings.py` |
| 22 | Secure defaults: SSL redirect, HSTS, secure cookies, referrer policy | Done | `prod.py`: redirect, HSTS 31 536 000 s, subdomains, preload, proxy header, secure cookies |
| 23 | Dockerfile: multi-stage | Done | `builder` → `production` in `Dockerfile` |
| 24 | Dockerfile: non-root user | Done | `USER flowforge` |
| 25 | Dockerfile: healthcheck | Done | `HEALTHCHECK` → `/health/live/` |
| 26 | Dockerfile: pinned base image | **Partial** | `python:3.13-slim` pinned by tag, not by digest |
| 27 | `.dockerignore` | Done | `.dockerignore` |
| 28 | Compose: healthchecks on all services | Done | `db`, `redis`, `rabbitmq` |
| 29 | Compose: `depends_on` with `service_healthy` | Done | `web`, `scheduler`, `worker` |
| 30 | Compose: named volumes | Done | `postgres_data` |
| 31 | Compose: no hardcoded secrets | **Partial** | `DJANGO_SECRET_KEY` is `${DJANGO_SECRET_KEY:-local-dev-secret}`; the DB password `flowforge` is hardcoded but only for the local dev stack |
| 32 | CI runs mypy | Done | `.github/workflows/ci.yml` lint job |
| 33 | CI runs a migration check | Done | `makemigrations --check --dry-run` |
| 34 | CI builds the Docker image | Done | `docker-build` job |
| 35 | CI actions pinned to immutable versions | Done | Four actions pinned to commit SHAs with version comments |
| 36 | Dependabot configured | Done | `.github/dependabot.yml` — `uv`, `github-actions`, `docker` |
| 37 | Dependency versions real and compatible | Done | `uv.lock` resolves 82 packages; suite runs in the locked env |
| 38 | Lock file present | Done | `uv.lock`; CI runs `uv lock --check` and `uv sync --locked` |
| 39 | Supported Python / Django versions documented | Done | `pyproject.toml` classifiers: 3.11/3.12/3.13, Django 5.1 |
| 40 | README claims only implemented features | Done | No badges; status line marks Phase 0 alpha; Celery described as interim |
| 41 | `docs/architecture/overview.md` exists | Done | Present, with component diagram and design principles |
| 42 | `docs/adr/` exists | Done | `docs/adr/ADR-007-celery-as-interim-task-infrastructure.md` |
| 43 | MkDocs configuration | Done | `mkdocs.yml` |
| 44 | Naming conflict investigated with evidence | Done | `docs/development/NAMING_ANALYSIS.md` — measured registry and GitHub results |
| 45 | Tests exist for new behaviour | Done | 13 tests across `tests/unit/test_health.py` and `tests/unit/test_settings.py` |
| 46 | Apache-2.0 license | Done | `LICENSE` |
| 47 | Conventional Commits | Done | `git log` on this branch |
| 48 | Docker stack verified end to end | **Missing** | Daemon offline; see UNVERIFIED |

## Step 5 — Secret scan

Tool: `detect-secrets 1.5.0` (gitleaks was not installed and could not be
fetched in this environment).

Scope: 45 tracked files in the working tree, plus every file in all 3 commits
in history (extracted with `git archive` and scanned).

**Result: 0 real secrets.** 9–10 findings per commit, all development
placeholders:

| File | Line | Type | Content | Verdict |
|------|------|------|---------|---------|
| `.env.example` | 8 | Basic Auth | `postgres://flowforge:flowforge@localhost/...` | Placeholder |
| `.env.example` | 12 | Basic Auth | `amqp://guest:guest@localhost:5672//` | RabbitMQ default |
| `.github/workflows/ci.yml` | 33 | Secret Keyword | `DJANGO_SECRET_KEY: ci-secret` | CI dummy |
| `.github/workflows/ci.yml` | 42 | Secret Keyword | `POSTGRES_PASSWORD: flowforge` | Ephemeral CI service |
| `.github/workflows/ci.yml` | 74 | Basic Auth | CI `DATABASE_URL` | Ephemeral CI service |
| `compose.yaml` | 8 | Secret Keyword | `POSTGRES_PASSWORD: flowforge` | Local dev stack |
| `compose.yaml` | 49, 51 | Basic Auth | Local `DATABASE_URL`, `CELERY_BROKER_URL` | Local dev stack |
| `flowforge/settings/base.py` | 42 | Secret Keyword | `django-insecure-dev-only-key-...` | Deliberate DEBUG-only fallback, `# noqa: S105` |
| `flowforge/settings/base.py` | 102 | Basic Auth | Default `DATABASE_URL` | Placeholder |

No `.env` file is tracked. No credentials, tokens or private keys were found
in any commit.

## Step 6 — Naming evidence (summary)

Full measured evidence is in `docs/development/NAMING_ANALYSIS.md`. Headline
results for the chosen name, **FlowForge**:

| Channel | Result |
|---------|--------|
| PyPI `flowforge` | Free — `GET /pypi/flowforge/json` → `{"message": "Not Found"}` |
| PyPI `flow-forge` | **Taken** — returns package metadata |
| npm `flowforge` | **Reserved** — package record exists with `versions: []`, published then unpublished in 2021 by another account; publishing is blocked |
| npm `flow-forge` | Free — HTTP 404 |
| GitHub | 12+ repositories; `relaticle/flowforge` (422★), `jialuechen/flowforge` (140★), `gufranthakur/FlowForge` (137★) |
| Commerce | `flowforgesoft.com` active; FlowForge (founded 2021-04-02, US) raised a $2.7M seed round and was rebranded **FlowFuse** |

**The name is not clear.** It is retained because the project owner decided to
retain it. No rename was performed. Re-evaluation triggers are listed in the
naming analysis.

## UNVERIFIED

The Docker gaps below were closed by running the stack in GitHub Actions,
which is the authoritative environment. Nothing here was verified on the
owner's machine.

| Item | Status |
|------|--------|
| Docker image build | **Verified in CI** — [39211983697](https://github.com/Abolfazlrwm/flowforge/actions/runs/39211983697), job `docker` |
| `docker compose up` (all six services) | **Verified in CI** — `migrate status=exited exit=0`, then `stack ready after 2 attempt(s)` |
| Docker healthcheck behaviour | **Verified in CI** — liveness and readiness probes asserted, all three dependency checks `ok` |
| GitHub Actions execution | **Verified** — `lint`, `test`, `docker` all PASS |
| `uv.lock` reproducibility on Linux/CI | **Verified** — CI installs with `uv sync --locked` and `uv lock --check` passes |
| Docker on the owner's machine | **Still UNVERIFIED.** No local daemon was available. |

## Verdict

**PASSED.**

Every check runs somewhere it can be trusted: lint, format, mypy strict, 13
behavioural tests, 100% statement coverage of the code that exists, migration
check, deploy check with zero issues, and a clean secret scan across the full
history — locally. Docker build, the full six-service compose stack and both
health endpoints — in GitHub Actions.

Three further defects (#25–#27) were found *by* that CI run and fixed rather
than explained away. #25 in particular had already produced one false green:
the stack passed while the migration race was still present.

The remaining limitations are the ones listed in
`docs/development/PROGRESS.md`, none of which block v0.1.0.
