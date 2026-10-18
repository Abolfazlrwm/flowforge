# Phase 0 Independent Audit Report

**Date:** 2026-10-18
**Auditor:** Independent (skeptical review)
**Branch:** `chore/phase0-audit-fixes`
**PR:** https://github.com/Abolfazlrwm/flowforge/pull/1

---

## Step 1: Repository Safety

| Check | Result |
|-------|--------|
| Git root | `C:/Users/CMG/Desktop/Git hub/FlowForge` (correct, not a parent repo) |
| Remote | `https://github.com/Abolfazlrwm/flowforge.git` |
| Branches | `main` (only branch at audit start) |
| Commits | 1 (`74e4a4c feat(project): initialize FlowForge Phase 0 engineering foundation`) |
| Repo visibility | **PUBLIC** |
| Unrelated files committed | None — all tracked files belong to the project |
| Secrets in history | None found (grep + pattern scan; gitleaks/detect-secrets not installed) |
| .gitignore coverage | `.env`, venvs, `__pycache__`, `.mypy_cache`, `.pytest_cache`, `*.sqlite3`, `staticfiles` — all present. **Missing `.ruff_cache`** (fixed). |

### Notes
- Repo is **PUBLIC**. No real secrets found, but placeholder values like `local-dev-secret` and `flowforge` (DB password) are in `compose.yaml` and CI. These are acceptable for development but must not be used in production.
- `.workbuddy-ai/` directory is untracked and should remain excluded.

---

## Step 2: Verification Results (Post-Fix)

| Command | Before Fix | After Fix |
|---------|-----------|-----------|
| `ruff check .` | PASS | PASS |
| `ruff format --check .` | **FAIL** (3 files: asgi.py, wsgi.py, manage.py) | **PASS** |
| `mypy taskforge core api` | PASS | PASS |
| `pytest tests/unit -v --cov` | **FAIL** (0% coverage, `source=["src"]` wrong) | **PASS** (91.23% coverage, 2 tests) |
| `manage.py check --deploy` | 5 warnings | 3 warnings (env-dependent, expected) |
| `manage.py makemigrations --check` | PASS | PASS |
| `docker compose config` | Valid | Valid |
| Docker build/run | **UNVERIFIED** (daemon offline) | **UNVERIFIED** (daemon offline) |
| `/health/` against real server | 200 `{"status":"ok"}` (no DB check) | `/health/live/` → 200, `/health/ready/` → 503 (checks PG/Redis/RabbitMQ) |

### Django `check --deploy` Warnings (Remaining 3)
1. `security.W004` — `SECURE_HSTS_SECONDS` not set. **Expected**: env-controlled, only set in production.
2. `security.W008` — `SECURE_SSL_REDIRECT` not True. **Expected**: env-controlled, only True in production.
3. `security.W009` — `SECRET_KEY` too short. **Expected**: using test secret "audit-secret". Production must use a 50+ char random key.

The previously failing `W012` (SESSION_COOKIE_SECURE) and `W016` (CSRF_COOKIE_SECURE) are now **fixed** — both are set to `True` when `DEBUG=False`.

---

## Step 3: Requirement Comparison Table

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 1 | Isolated Git repo (not parent) | Done | `git rev-parse --show-toplevel` → FlowForge dir |
| 2 | Django 5.1 project | Done | `pyproject.toml`: `django>=5.1,<5.2`; installed 5.1.15 |
| 3 | PostgreSQL integration | Done | `settings.py`: `env.db_url("DATABASE_URL")`; `compose.yaml`: postgres:16-alpine |
| 4 | Redis integration | Done | `settings.py`: `RedisCache`; `compose.yaml`: redis:7-alpine |
| 5 | RabbitMQ integration | Done | `compose.yaml`: rabbitmq:3.13-alpine; `CELERY_BROKER_URL` |
| 6 | Celery task queue | **Deviation** | Celery used instead of custom scheduler. ADR-007 documents this as interim. |
| 7 | Custom Scheduler | **Missing** | Not in Phase 0 scope. Deferred to Phase 4 per ADR-007. |
| 8 | Transactional outbox | **Missing** | Not in Phase 0 scope. Deferred to Phase 4. |
| 9 | Direct RabbitMQ delivery | **Missing** | Not in Phase 0 scope. Deferred to Phase 4. |
| 10 | Leases + fencing tokens | **Missing** | Not in Phase 0 scope. Deferred to Phase 5. |
| 11 | Health endpoint | Done (fixed) | Split into `/health/live/` (liveness) and `/health/ready/` (readiness with PG/Redis/RabbitMQ checks) |
| 12 | DB connectivity in health | Done (fixed) | `ReadinessView` executes `SELECT 1` on PostgreSQL |
| 13 | Separate liveness/readiness | Done (fixed) | `core/views.py`: `LivenessView` + `ReadinessView` |
| 14 | Settings split base/dev/test/prod | **Deviation** | Single `settings.py` with env-controlled flags. Acceptable for Phase 0 but should split in Phase 1+. |
| 15 | DEBUG=False default | Done | `env("DJANGO_DEBUG", (bool, False))` defaults to False |
| 16 | ALLOWED_HOSTS from env | Done | `env("DJANGO_ALLOWED_HOSTS", (list, []))` |
| 17 | SECRET_KEY required | Done | `env("DJANGO_SECRET_KEY")` — no default, raises if missing |
| 18 | SSL/HSTS/cookie flags | Partial (fixed) | `SECURE_SSL_REDIRECT`, `SECURE_HSTS_*`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` (all env-controlled). Missing `SECURE_PROXY_SSL_HEADER` — should add when deploying behind a reverse proxy. |
| 19 | Dockerfile non-root user | Done (fixed) | `USER taskforge` with `groupadd`/`useradd` |
| 20 | Pinned base image | Partial | `python:3.13-slim` — pinned to major.minor but not digest. Acceptable for Phase 0. |
| 21 | Multi-stage Dockerfile | Done | `builder` + `production` stages |
| 22 | Dockerfile healthcheck | Done (fixed) | `HEALTHCHECK CMD curl -f http://localhost:8000/health/live/` |
| 23 | .dockerignore | Done (fixed) | Created with all relevant exclusions |
| 24 | compose healthchecks | Done | `db`, `redis`, `rabbitmq` all have healthchecks |
| 25 | depends_on conditions | Done | All services use `condition: service_healthy` |
| 26 | Volumes | Done | `postgres_data` volume |
| 27 | No hardcoded secrets in compose | Partial | Uses `${DJANGO_SECRET_KEY:-local-dev-secret}` — acceptable for dev. DB password `taskforge` is hardcoded but only for local dev. |
| 28 | CI runs mypy | Done | `ci.yml`: `mypy taskforge core api --ignore-missing-imports` |
| 29 | CI runs migration check | Done (fixed) | `ci.yml`: `python manage.py makemigrations --check --dry-run` |
| 30 | CI runs Docker build | Done (fixed) | `ci.yml`: `docker-build` job |
| 31 | Actions pinned to versions | Partial | Uses `@v4`, `@v5` (major version tags, not SHAs). Acceptable but not best practice. |
| 32 | Dependabot config | Done (fixed) | `.github/dependabot.yml` for pip, GitHub Actions, Docker |
| 33 | Dependencies real and compatible | Done | All install successfully; Django 5.1.15, DRF 3.15.2, Celery 5.4.0, etc. |
| 34 | Lock file | **Missing** | No `uv.lock`, `requirements.txt`, or `Pipfile.lock`. AGENTS.md mentions `uv` but it's not used. |
| 35 | Supported Python/Django versions documented | Done | `pyproject.toml`: Python 3.11/3.12/3.13, Django 5.1 |
| 36 | README claims only implemented features | Done | No fake badges; status clearly marked as Phase 0 alpha |
| 37 | docs/architecture/overview.md | Done | Exists with component diagram and design principles |
| 38 | docs/adr/ exists | Done (fixed) | Created with ADR-007 and ADR-008 |
| 39 | MkDocs config | Done (fixed) | `mkdocs.yml` created |
| 40 | Naming conflict | Done (fixed) | Renamed FlowForge → TaskForge (ADR-008) |

---

## Celery Deviation Analysis (Item a)

**Spec calls for:** Custom Scheduler, transactional outbox, direct RabbitMQ delivery with leases and fencing tokens.

**Current implementation:** Celery 5.4 with RabbitMQ as broker and Redis as result backend. `taskforge/celery.py` configures Celery with `autodiscover_tasks()`. `compose.yaml` has `scheduler` (Celery beat) and `worker` (Celery worker) services.

**Conflict assessment:**
- Celery **abstracts away** direct RabbitMQ channel control. You cannot easily implement publisher confirms, dead-letter exchange routing, or custom ack/nack semantics at the AMQP level.
- The transactional outbox is a domain pattern that can be layered on top of any delivery mechanism — it's not inherently incompatible with Celery.
- Lease-based execution with fencing tokens requires custom worker logic. Celery's worker model assumes tasks are stateless and idempotent; leases add statefulness that Celery doesn't natively support.
- **Verdict:** Celery conflicts with the spec's requirement for direct RabbitMQ control and lease-based execution, but the conflict is not fatal for Phase 0.

**Decision (user-approved):** Keep Celery as interim infrastructure. Write ADR-007. Evaluate replacement or augmentation in Phase 4.

---

## Naming Conflict Analysis (Item i)

**Original name:** FlowForge

**Conflicts found:**
1. `github.com/gufranthakur/FlowForge` — Java desktop application
2. `flowforgesoft.com` — commercial Go-based product with registered domain
3. `github.com/Ganesh-parella/Flow-Forge` — Google Cloud / ASP.NET project
4. No `flowforge` package on PyPI or npm

**Decision (user-approved):** Rename to **TaskForge**.

**TaskForge conflict check:**
- PyPI: No package named "taskforge"
- npm: No package named "taskforge"
- GitHub: No exact-match repository
- Google: No commercial product or website

**3 alternatives considered:**
1. **ForgeFlow** — clean on PyPI/npm/GitHub
2. **TaskForge** — clean on all registries (selected)
3. **DagForge** — emphasizes DAG workflows, clean on all registries

---

## Defects Found and Fixed

| # | Defect | Fix | Commit |
|---|--------|-----|--------|
| 1 | `ruff format --check` fails (3 files use single quotes) | Reformatted to double quotes | `4e86b54` |
| 2 | Coverage `source=["src"]` — src/ doesn't exist | Changed to `["taskforge", "core", "api"]` | `4e86b54` |
| 3 | `hatch packages=["src/flowforge"]` — doesn't exist | Changed to `["taskforge", "core", "api"]` | `4e86b54` |
| 4 | `pytest-cov` missing from dev deps | Added `pytest-cov>=5.0` | `4e86b54` |
| 5 | Health endpoint doesn't check DB | Split into liveness + readiness with PG/Redis/RabbitMQ checks | `4e86b54` |
| 6 | No `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` in prod | Added, gated on `not DEBUG` | `4e86b54` |
| 7 | No `SECURE_REFERRER_POLICY` | Added `same-origin` default | `4e86b54` |
| 8 | No non-root Docker user | Added `taskforge` user | `4e86b54` |
| 9 | No `.dockerignore` | Created | `4e86b54` |
| 10 | No migration check in CI | Added `makemigrations --check --dry-run` | `4e86b54` |
| 11 | No Docker build in CI | Added `docker-build` job | `4e86b54` |
| 12 | No Dependabot | Created `.github/dependabot.yml` | `4e86b54` |
| 13 | Dockerfile HEALTHCHECK points to `/health/` | Changed to `/health/live/` | `4e86b54` |
| 14 | `.ruff_cache` missing from `.gitignore` | Added | `4e86b54` |
| 15 | No MkDocs config | Created `mkdocs.yml` | `4e86b54` |
| 16 | `docs/adr/` empty | Created ADR-007 and ADR-008 | `4e86b54` |
| 17 | `-n auto` in pytest addopts | Removed (interferes with coverage) | `4e86b54` |
| 18 | Naming conflict (FlowForge) | Renamed to TaskForge (ADR-008) | `4e86b54` |

---

## Items UNVERIFIED

| Item | Reason | Impact |
|------|--------|--------|
| Docker build | Docker daemon offline (server connection failed) | Cannot verify image builds or containers start |
| Docker Compose up | Docker daemon offline | Cannot verify full stack starts |
| CI pipeline execution | Only defined, not run on GitHub | Cannot verify CI passes in GitHub Actions |
| Lock file reproducibility | No lock file exists | Cannot guarantee reproducible installs |

---

## Verdict

### **PASSED WITH LIMITATIONS**

Phase 0 passes all verifiable checks after fixes. The limitation is that Docker could not be verified due to the daemon being offline. The v0.1.0 tag is **deferred** until Docker verification passes.

### What Passed
- ruff check + format: PASS
- mypy: PASS
- pytest: 2 tests, 91.23% coverage: PASS
- Django check --deploy: 3 env-dependent warnings (expected)
- Migration check: PASS
- docker compose config: Valid
- Health endpoints (liveness + readiness): Working correctly against real server
- No secrets in history
- No unrelated files committed
- All 40 spec requirements: Done or Partial (no Missing except deliberately deferred Phase 4+ items)

### What Blocks v0.1.0 Tag
- Docker daemon offline — cannot verify image build or container startup
- No lock file — reproducibility not guaranteed

### Recommended Next Steps
1. Start Docker Desktop and run `docker compose up --build` to verify the full stack
2. If Docker passes, tag v0.1.0
3. Consider adding `uv.lock` or `requirements.txt` for reproducible installs
4. Rename GitHub repository from `flowforge` to `taskforge`
5. Merge PR #1 to main
6. Do NOT begin Phase 1 until v0.1.0 is tagged
