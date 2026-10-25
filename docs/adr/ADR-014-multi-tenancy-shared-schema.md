# ADR-014: Multi-Tenancy via a Shared Schema and Per-Organization Scoping

**Date:** 2026-10-23
**Status:** Accepted
**Related:** Phase 8 — Security & Multi-Tenancy (v0.9.0); the tenant-isolation
suite (`tests/unit/api/test_tenant_isolation.py`); the STRIDE model
([`docs/security/threat-model.md`](../security/threat-model.md)); ADR-011 (the
outbox and scheduler already write one row per tenant action)

## Context

Until v0.8.1 FlowForge had a single implicit tenant: a run was visible to the
user who started it, and definitions were global. That is not a security model —
two customers could not share a deployment, and a definition name was global, so
"etl v1" could only exist once. Phase 8 requires:

- every model and endpoint scoped by organization;
- roles (owner/admin/member/viewer) with per-organization authority;
- API keys that are hashed at rest, scoped, expirable and revocable;
- an append-only audit log;
- secret redaction;
- workers, the scheduler and the publisher carrying tenant context.

The design had to preserve the existing guarantees — the fencing-token worker
protocol, the transactional outbox, the append-only event log — and stay runnable
on both PostgreSQL (CI) and SQLite (local tests).

## Decision

**One shared schema. An `organization` foreign key on every tenant row. Every
read goes through an organization-scoped manager.**

Concretely:

1. **Models.** `Organization` and `Membership` (a user's role in one
   organization) are new. `Workflow`, `WorkflowRun`, `TaskRun`, `TaskRunEvent`,
   `ScheduledTrigger`, `OutboxMessage`, `IdempotencyKey`, `ApiKey` and `AuditLog`
   each carry `organization`.
2. **Scoping by construction.** `OrganizationScopedManager.for_organization(org)`
   is the sanctioned read path. Views and services call it; a second view cannot
   forget the filter because there is no unscoped read helper to reach for. A
   row from another tenant is therefore absent from the queryset, and a lookup by
   its id returns **404, not 403** — the row's existence does not leak.
3. **Per-organization uniqueness.** A definition is unique on
   `(organization, name, version)`, so two tenants may each publish `etl v1`.
   An idempotency key is unique on `(organization, user, key)`, so a replay in
   one tenant can never return another tenant's run.
4. **RBAC.** `viewer < member < admin < owner`. The role is read from the
   caller's membership in the active organization. Global staff (`is_staff`) is a
   deliberate operational bypass for reads, and a request authenticated by an API
   key never gets it.
5. **Active organization resolution.** An API key fixes the organization. A
   session/token/JWT caller selects it with `X-Organization: <slug>` or, when
   they belong to exactly one organization, implicitly. An ambiguous or
   unauthorized selection is a 403, never a silent choice.
6. **API keys.** `ff_<hex-prefix>_<hex-secret>`; only `make_password(secret)` is
   stored. The prefix is the indexed lookup handle. Scopes are `read < write <
   admin` and are checked **in addition to** the creator's role, so a key cannot
   exceed its creator. Rotation revokes the old key before issuing the new one.
7. **Audit log.** `AuditLog` rows are append-only, enforced by a database
   trigger, and metadata is redacted before it is written.
8. **Reversible migration.** The schema migration adds nullable `organization`
   columns; a data migration creates a default organization and backfills every
   existing row, and its reverse detaches them and removes the organization.
   Nullable is what makes the reverse possible without deleting data; the
   application always populates the column for new rows.

## Consequences

**Good.**

- A single deployment serves many tenants, and the isolation property is testable
  per endpoint (the suite asserts 404 for every cross-tenant id).
- Per-tenant definition names and idempotency keys work without collision.
- The audit log and API keys are real security controls, not conventions.
- The migration is reversible, so a rollback does not lose tenant data.

**Costly / accepted.**

- Every query must go through the scoped manager. A raw `Model.objects.all()` in
  a new view is a bug; the isolation suite is what catches it.
- The `organization` column is denormalized onto `TaskRun`, `TaskRunEvent`,
  `OutboxMessage` and `ScheduledTrigger` (rather than reached through a join) so
  the manager can filter without a join and the scheduler/publisher can process
  rows without loading the parent. The cost is that the column must be kept in
  step at creation; the trigger and scheduler do that in one place each.
- Staff bypass is a cross-tenant read path. It is intentional and narrow, and API
  keys are explicitly excluded from it.
- The database does not enforce "organization matches the parent" (e.g. a
  `TaskRun` whose organization differs from its run's). A `CHECK` cannot express
  a cross-row invariant; the service layer sets both from one value, and a
  mismatch would be a bug, not a supported state. This is a candidate for a
  database trigger in a later phase.

## Alternatives considered

- **Schema-per-tenant.** Strong isolation, but every migration must run N times,
  connection routing becomes application logic, and the test suite would need one
  schema per test. Rejected for operational complexity at this stage.
- **Database-per-tenant.** Strongest isolation and per-tenant backup, but the
  operational cost (provisioning, migrations, connection pools) is out of
  proportion to the project's current scale. Rejected.
- **PostgreSQL row-level security (RLS).** A good defence-in-depth layer, and the
  natural next step. Rejected *for now* because the unit suite runs on SQLite,
  which has no RLS, so the primary boundary would be untested locally and the
  tests would diverge from the deployed behaviour. Application-level scoping is
  portable and testable on both. RLS is recorded as a follow-up, not a
  replacement.
- **A tenant column only on `Workflow`, with everything else reached by join.**
  Simpler schema, but the scheduler and publisher query task runs and outbox rows
  directly; forcing a join through `Workflow` on every poll would add cost to the
  hottest path and make the scoped manager unable to filter without a join.
  Rejected.

## Follow-ups

**Done since this ADR was accepted:**

- **Membership-management endpoints and audit entries for role changes.** Shipped
  in `v0.9.1`: `GET/POST /api/v1/members/` and
  `PATCH/DELETE /api/v1/members/<uuid>/`, with owner/admin rules, tenant
  isolation, and `member.added` / `member.role_changed` / `member.removed` audit
  entries. The repudiation row in the threat model was updated accordingly.
- **A configurable throttle.** `THROTTLE_AUTH_RATE` / `THROTTLE_TRIGGER_RATE` /
  `THROTTLE_READ_RATE` landed in Phase 9 with the shipped defaults unchanged and
  asserted by a test, so the per-process limitation above can be tuned without
  editing settings.

**Still open, in priority order:**

- PostgreSQL RLS as a second boundary, with the tenant id set per connection.
- A database-level check (trigger) that a child row's organization matches its
  parent's.
- Encryption at rest is assumed from the database; it is not implemented here.
- A shared cache for throttling, so the limit is global rather than per-process.
