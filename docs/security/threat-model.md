# Threat model (STRIDE)

This is the security threat model for FlowForge as of **v0.11.0**. It is a living
document: every threat below names the mechanism that mitigates it and the test
or code that is the evidence. A mitigation with no evidence is marked
**UNVERIFIED** rather than assumed.

Phase 11 re-reviewed this model against the code that actually ships. Three
claims that were true at v0.9.0 had gone stale and were corrected rather than
carried forward — they are marked **[corrected]** below, with the commit that
made them true.

## Scope and assets

| Asset | Why it matters |
| --- | --- |
| Workflow definitions | They decide what executes; tampering changes every future run. |
| Workflow runs and task results | They contain business data and are tenant-confidential. |
| Secrets in task `config` | A password or token a task needs to execute. |
| API keys | A long-lived credential that acts for a tenant. |
| Memberships and roles | They decide who may do what inside a tenant. |
| The audit log | The record of who did what; it must not be rewritable. |
| The outbox / broker messages | They carry dispatch instructions to workers. |
| The container image | What a deployment actually runs; a tampered image is arbitrary code. |

## Trust boundaries

1. **Client → API.** Untrusted input crosses here; TLS terminates at the proxy.
2. **API → database.** The application is the only writer; the database is the
   last line of defence for invariants (constraints, triggers).
3. **Scheduler/publisher → broker.** A compromised broker could replay or drop
   messages; the outbox and fencing tokens bound the damage.
4. **Broker → worker.** A message is untrusted input to the worker; handlers are
   a trusted, in-process registry, never imported from the message.
5. **Operator → CLI.** A local operator has database access; the audit log is
   append-only so their actions are recorded, not hidden.
6. **Registry → runtime.** An image pulled from a registry is trusted by digest;
   a tag alone is a mutable pointer. See *Supply chain* below.

## STRIDE

### Spoofing

| Threat | Mitigation | Evidence |
| --- | --- | --- |
| A stolen API key is replayed | Keys are stored only as `make_password` hashes; a database dump cannot be replayed. Keys expire and can be revoked. | `core/services/api_keys.py`, `tests/unit/test_api_keys_service.py` |
| A deactivated user keeps using a token | `IsAuthenticatedAndActive` checks `is_active` on every request. | `tests/unit/api/test_auth.py` |
| A key outlives its creator's membership | Authentication refuses a key whose creator is no longer a member. | `tests/unit/api/test_api_keys.py::test_an_orphaned_key_is_rejected` |
| A caller claims another tenant | The active organization is resolved from the key or a membership, never from the request body. | `api/tenancy.py`, `tests/unit/api/test_tenant_isolation.py` |

**Residual:** there is no MFA. A stolen *user* token is valid until it expires
(JWT 15 minutes) or is deleted (DRF token). **UNVERIFIED:** no test exercises a
stolen-token scenario end to end.

### Tampering

| Threat | Mitigation | Evidence |
| --- | --- | --- |
| A definition is overwritten in place | `(organization, name, version)` is unique and publishing different content under an existing version is a 409. | `tests/integration/test_concurrent_publish.py`, `tests/unit/api/test_workflows.py` |
| An audit row is rewritten or deleted | A database trigger raises on `UPDATE` and `DELETE`; the application only inserts. | `core/migrations/0015_audit_log_append_only.py`, `tests/unit/test_audit_log.py` |
| A stale worker overwrites a newer attempt | The fencing token is part of the conditional `UPDATE`; a stale token matches zero rows. | `core/repositories/transitions.py`, `tests/integration/test_concurrent_transitions.py` |
| A task result is injected via a replayed dispatch | Dispatch ids are de-duplicated; a redelivered message is recognised. | `core/worker/consumer.py`, `tests/integration/test_worker.py` |

### Repudiation

| Threat | Mitigation | Evidence |
| --- | --- | --- |
| A caller denies publishing, triggering, requeueing or rotating a key | Each mutating action writes an audit entry with the actor's label snapshot. | `core/services/audit.py`, `tests/unit/api/test_audit.py` |
| Deleting the user erases who acted | `actor_label` is a snapshot; `actor` is `SET_NULL`. | `tests/unit/test_audit_log.py` |
| A membership change is denied afterwards | `member.added`, `member.role_changed` and `member.removed` are audited. **[corrected]** the v0.9.0 model recorded this as a gap because there was no membership endpoint; v0.9.1 added the endpoints and the entries. | `api/views.py`, `tests/unit/api/test_members.py` |

### Information disclosure

| Threat | Mitigation | Evidence |
| --- | --- | --- |
| Reading another tenant's run, definition, event log or task run | Every read goes through `OrganizationScopedManager.for_organization`; another tenant's id is a 404, not a 403. | `api/views.py`, `tests/unit/api/test_tenant_isolation.py` |
| A secret in task `config` reaches a client or a log | `redact_mapping` redacts by key name and by `secret: true`; applied to the API's task config and audit metadata. | `core/security.py`, `tests/unit/test_redaction.py` |
| A key's secret leaks through the list endpoint | Listing returns metadata only; the plaintext is returned once, at creation or rotation. | `tests/unit/api/test_api_keys.py` |
| Error messages distinguish "exists" from "forbidden" across tenants | Cross-tenant lookups are scoped out of the queryset, so the answer is a uniform 404. | `tests/unit/api/test_tenant_isolation.py` |

**Residual:** the dispatch payload carries the real `config` because the worker
must execute it; broker and worker logs are trusted infrastructure. **UNVERIFIED:**
no test asserts that a broker log does not contain a secret — the worker does not
log the payload, but that is a code reading, not a test.

### Denial of service

| Threat | Mitigation | Evidence |
| --- | --- | --- |
| A client floods the API | Per-scope throttles (`auth` 10/min, `trigger` 60/min, `read` 300/min), configurable per deployment. | `flowforge/settings/base.py`, `tests/unit/api/test_throttling.py` |
| A task hangs and holds a worker forever | Leases expire; the reaper retries or buries the task; the heartbeat extends a live lease. | `core/repositories/transitions.py`, `tests/integration/test_failure_injection.py` |
| A retry storm | Exponential backoff with a cap and optional jitter. | `core/domain/retry.py`, `tests/unit/domain/test_retry.py` |

**Residual:** throttling is per-process, not global; a distributed deployment
needs a shared cache (Redis is configured but the test suite uses a local cache).
**[corrected]** the v0.9.0 model recorded "no load test" as unverified. There is
now a Locust suite with committed raw results
([`docs/development/BENCHMARKS.md`](../development/BENCHMARKS.md)); it measures
capacity, not a denial-of-service threshold, so "what request rate this
deployment survives" remains **UNVERIFIED**.

### Elevation of privilege

| Threat | Mitigation | Evidence |
| --- | --- | --- |
| A viewer triggers or publishes | The role matrix gates every write: viewer < member < admin < owner. | `api/permissions.py`, `tests/unit/api/test_tenant_isolation.py` |
| An API key exceeds its creator's authority | Both the key's scope and the creator's role are checked. | `tests/unit/api/test_api_keys.py` |
| A key created by staff reads across tenants | A key never inherits the staff cross-tenant bypass; it is always scoped to its organization. | `api/views.py::_is_staff_bypass`, `tests/unit/api/test_tenant_isolation.py` |
| A user escalates by naming another organization | The `X-Organization` header is honoured only for an organization the caller is a member of. | `api/tenancy.py`, `tests/unit/api/test_permissions.py` |

**Residual:** the `owner` role is all-powerful inside its tenant (including
issuing keys); there is no separation of duties between owner and admin beyond
the rank. **UNVERIFIED:** no test covers a compromised owner.

## Supply chain and code patterns

| Control | What it does | Evidence |
| --- | --- | --- |
| `bandit` | Flags risky patterns in first-party code. The five intentional findings (dev-only secret key, two fixture passwords, retry jitter, the metrics bind address) are annotated at the line with a reason. | `pyproject.toml`, CI `lint` job |
| `pip-audit` | Fails the build on a known vulnerability in the locked dependency set. It forced the DRF 3.18.3 upgrade (CVE-2026-114136/114137/114138). | CI `lint` job |
| `uv.lock` | Every install is from the lock; CI runs `uv lock --check`. | `.github/workflows/ci.yml` |
| `gitleaks` | Scans the full commit history and the working tree for credentials. | CI `secrets` job, `.gitleaks.toml` |
| `Trivy` | Scans the built image for vulnerabilities, secrets and misconfigurations. | CI `supply-chain` job |
| `cosign` (keyless) | Signs the published image; the signature's identity is the workflow's OIDC token, recorded in the Rekor log. | CI `supply-chain` job |
| CycloneDX SBOM | Names every component in the published image. | CI `supply-chain` job, attached to the release |
| Digest pinning | Base images are referenced by digest, not by mutable tag. | `Dockerfile`, Dependabot `docker` ecosystem |

### A finding that changed a decision

The secret scan was first written against `gitleaks/gitleaks-action`. Reading
that action's `LICENSE.txt` showed it is distributed under a **proprietary EULA**
that requires a paid license key for repositories owned by a GitHub Organization
and forbids redistribution as part of a product. The repository currently sits
under a personal account, so it would have worked today and broken the day it
moved.

The scan uses the **gitleaks binary** instead, which is MIT. Same scanner, same
version, same rules, same config, no license dependency. This is recorded because
the obvious choice was the wrong one and nothing in the action's name or
description hints at the terms.

### Residual risks in the container image

Trivy against the pinned `python:3.13-slim` digest reports **48 HIGH/CRITICAL**
findings. They split three ways, and only one of them was fixable in this
repository:

- **4 fixable, all in the base image's own `site-packages`.** The production
  image runs `/opt/venv/bin/python`, whose `pyvenv.cfg` sets
  `include-system-site-packages = false`, so those packages are unreachable —
  but unreachable is not absent, and Trivy scans layers. The Dockerfile now
  deletes `/usr/local/lib/python3.13/site-packages` in the production stage,
  which removes the findings and shrinks what code execution could reach.
  Verified: the venv ships urllib3 2.8.0 and msgpack 1.2.3 against the base
  image's vulnerable 2.7.0 and 1.1.2.
- **44 unfixable, all in Debian base OS utilities** — `util-linux`, `perl-base`,
  `ncurses`, `login`, `mount`, `libsystemd0` and similar. None has a fixed
  version available in Debian 13.7, so `apt-get upgrade` does not clear them,
  and none is a package the runtime executes: the production stage installs only
  `libpq5` and `curl`, both of which scan clean. **Accepted residual risk.**
- **Not reachable through the application.** The two are unrelated findings: the
  image's OS packages are inherited from upstream and no application change
  affects them.

The CI gate therefore fails on **CRITICAL** and publishes the full HIGH list as a
SARIF artifact rather than failing on it — a gate that is permanently red is a
gate nobody reads. A distroless or Wolfi base would remove the 44; that is a
deliberate re-platforming decision, not a patch.

## What this model does not cover

- **Encryption at rest / in transit.** Assumed to be provided by the database and
  the reverse proxy; not implemented by FlowForge.
- **PostgreSQL row-level security.** Application-level scoping is the current
  boundary; RLS is a candidate for defence in depth (ADR-014, follow-up).
- **Insider with database access.** The append-only trigger stops casual
  rewrites, but a superuser can drop the trigger. The audit log is not
  tamper-evident against a database owner.
- **Key management for cosign.** Keyless signing removes a stored private key;
  it also means verification depends on the Rekor transparency log being
  available and on GitHub's OIDC issuer remaining the signer of record.
- **Runtime behaviour of the container.** The image is non-root with a
  read-only-friendly layout, but no admission controller, seccomp profile or
  runtime policy ships with this repository.
