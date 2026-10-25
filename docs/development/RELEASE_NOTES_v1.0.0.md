# FlowForge v1.0.0 — release notes (DRAFT)

> **DRAFT — not published.** `v1.0.0` requires the owner's explicit approval
> before it is tagged (owner decision 2026-10-22, recorded in
> [`DECISIONS.md`](DECISIONS.md)). This file is that draft for review. Do not tag
> or publish until it is approved; see [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

## Summary

FlowForge is a distributed workflow orchestration engine for DAG-shaped jobs,
built on Django 5.2 LTS, PostgreSQL, RabbitMQ and Redis. It makes the hard parts
of distributed execution explicit — leases, fencing tokens, a transactional
outbox and at-least-once delivery — instead of hiding them behind a task
decorator.

**v1.0.0 is the first stable release.** It is the culmination of Phases 0–10
(shipped as `v0.1.0` … `v0.11.0`) plus Phase 11, *Production Hardening*: the
operational surface that makes the engine deployable and operable — a security
review, safe migrations, Kubernetes, backups, high availability, an end-to-end
suite, and a signed, SBOM'd supply chain.

> **The 1.0 stability commitment is an owner decision.** Tagging `v1.0.0` signals
> a SemVer stability promise for the REST API and the public contract. Confirm
> that promise is intended before tagging; if not, cut `v0.12.0` instead and keep
> the 1.0 line for later.

## What FlowForge gives you

- **Describe, validate, publish, trigger** a workflow over a versioned REST API,
  with a Python SDK (`flowforge-client`) and a `flowforge` CLI.
- **Correct distributed execution.** A scheduler promotes dependency-satisfied
  tasks into a transactional outbox; a publisher delivers to RabbitMQ with
  publisher confirms; a worker claims each task run with a fencing token, runs a
  trusted handler, writes the durable outcome, and only then acks.
- **Reliability.** Bounded retries with exponential backoff and full jitter, a
  terminal `DEAD` state with manual requeue, lease recovery that bumps the fencing
  token, and a failure model (`docs/architecture/failure-model.md`) that maps
  every failure mode to a test that proves the recovery.
- **Multi-tenancy and security.** Per-organization scoping by construction
  (ADR-014), RBAC, hashed/scoped/rotatable API keys, an append-only audit log
  protected by a database trigger, and secret redaction.
- **Observability.** Correlation ids and traces that follow a run across every
  process, per-process Prometheus metrics, and Grafana dashboards as code.
- **Operability.** Docker Compose and a Kubernetes Helm chart, a backup/restore
  runbook with a CI restore drill, high-availability guidance, and a signed,
  SBOM'd, digest-pinned container image.

## New since v0.11.0 (Phase 11 — Production Hardening)

- **Security review** — gitleaks over the full history with push protection;
  Trivy image scanning with a `CRITICAL` gate; a STRIDE threat model; an
  overhauled `SECURITY.md` with a private disclosure process.
- **Migration safety** — a CI test that upgrades from the `v0.9.0` schema with
  data present, and a per-migration zero-downtime classification
  (`docs/deployment/migrations.md`).
- **Kubernetes** — a Helm chart and rendered manifests, validated in CI with
  `helm lint` and `kubeconform -strict`, plus an on-demand kind smoke test.
- **Backup and restore** — a runbook and a CI drill that dumps, wipes, restores,
  checks row counts, and runs a workflow end to end.
- **High availability** — `docs/deployment/ha.md`, honest about what is proven
  and what is operator-validated.
- **End-to-end suite** — `tests/e2e/` drives the REST API, SDK and CLI against
  the Compose stack in CI.
- **Supply chain** — a CycloneDX SBOM attached to each release, keyless cosign
  signing recorded in Rekor, build provenance, and a digest-pinned base image.
- **Documentation site** — the MkDocs site is published to GitHub Pages from
  `main`.

## Install

```bash
git clone https://github.com/Abolfazlrwm/flowforge.git
cd flowforge
docker compose up --build -d
curl -s http://localhost:8000/health/ready/   # {"status": "ready", ...}
```

Or the Python SDK / CLI:

```bash
pip install ./sdk          # flowforge-client, no runtime dependencies
export FLOWFORGE_URL=http://localhost:8000
export FLOWFORGE_TOKEN=<token>
flowforge trigger my-workflow --version 1
```

## Verify this release

The image is signed; verify without a key:

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/Abolfazlrwm/flowforge/.github/workflows/ci.yml' \
  --certificate-oidc-issuer-regexp 'https://token.actions.githubusercontent.com' \
  ghcr.io/abolfazlrwm/flowforge:v1.0.0
```

The release carries `flowforge-sbom.cdx.json`, a CycloneDX inventory of every
component in the image.

## Honest scope

- **At-least-once, never exactly-once.** Duplicate suppression is best-effort
  (idempotency keys + fencing tokens + `dispatch_id`); two concurrent executions
  of the same key can both run.
- **No arbitrary user code.** Task handlers are a fixed, trusted set; a task
  definition is data, not code.
- **Single writable PostgreSQL primary.** The app has no read/write splitting;
  HA needs a standby to promote (see `docs/deployment/ha.md`).
- Full list: `docs/development/PROGRESS.md` → *Known limitations*.

## Upgrade notes

Every migration is additive (no table rewrite), so a rolling upgrade is safe:
back up, run `manage.py migrate` as a one-shot job, then roll the new pods.
See `docs/deployment/migrations.md`. No action is required to upgrade from
`v0.11.0` beyond the migration.
