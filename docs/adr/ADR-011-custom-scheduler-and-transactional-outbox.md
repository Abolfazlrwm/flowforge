# ADR-011: Custom Scheduler and Transactional Outbox; Celery Removed in Phase 4

**Date:** 2026-10-20
**Status:** Accepted — Owner decision, 2026-10-20
**Supersedes:** [ADR-007](ADR-007-celery-as-interim-task-infrastructure.md) (interim
status ends; the decision ADR-007 deferred to Phase 4 is made here)
**Related:** Phase 4 — Scheduler (v0.5.0)

> **Owner decision, 2026-10-20.** This ADR was written as *Proposed* and the
> owner approved it as written, on the first option presented. The consequences
> section is therefore binding: five dependencies go, the `scheduler` and
> `worker` compose services are rewired in the same PR as the removal, and
> nothing executes a task end to end until Phase 5 ships the worker. Recorded so
> no later reader has to infer whether that gap was deliberate.

## Context

Phases 0–3 built and verified the correctness machinery. Phase 3 shipped the HTTP
surface, so a run can be created and read remotely, but nothing claims a task
run: `trigger` writes every `TaskRun` in its initial state and stops. Phase 4
closes that gap with a scheduler, and with it comes the decision ADR-007
deliberately left open.

ADR-007 adopted Celery as *interim* infrastructure, and stated the terms of the
revisit precisely: whether Celery's abstraction conflicts with lease-based
execution, whether direct RabbitMQ control is needed, and whether fencing tokens
can be layered onto Celery task headers. Those are the right questions, and they
can now be answered against real code rather than a plan.

What already exists, and therefore constrains the answer:

1. **`claim_task_run`** (in `core/repositories/transitions.py`) — one conditional
   `UPDATE ... WHERE id=? AND state='ready'` that also increments the fencing
   token and sets the lease. It returns `None` to a loser. The database is the
   sole arbiter of which worker wins, and that guarantees exactly-one execution
   without any broker feature.
2. **`finish_task_run`** requires `fencing_token=<token>`, so a resurrected
   worker's write matches zero rows. Again: PostgreSQL, not the broker.
3. **`DependencyGraph.ready_tasks(completed)`** — the eligibility question, whose
   docstring already names Phase 4 as the caller.
4. **Lease columns plus a partial index on `lease_expires_at`**, and a CHECK
   constraint making a lease exist exactly while a task is `RUNNING`. The lease
   reaper's query path was designed in Phase 1.
5. **Phase 3's service layer** — a view that calls one service and serialises, so
   a scheduler that calls one service and publishes is the same shape.

The Celery footprint, if it goes, is five entries: `flowforge/celery.py`, three
lines in `flowforge/__init__.py`, two `command:` lines in `compose.yaml`, one
dependency in `pyproject.toml`, and a Dependabot ignore rule.

## Decision

**Remove Celery in Phase 4 and build a custom scheduler with a transactional
outbox publishing directly to RabbitMQ.**

Three processes, each owning exactly one guarantee:

| Process | Owns | Reads/writes |
| --- | --- | --- |
| **Scheduler** | *Which* tasks dispatch | eligibility query → outbox row |
| **Publisher** | *That* a message is never lost | outbox rows → RabbitMQ, with confirms |
| **Worker** (Phase 5) | *Who* executes | consumes → `claim_task_run` → execute → `finish_task_run(fencing_token=…)` |

The division is the decision's substance, so it is stated plainly: **the worker
claims, not the scheduler.** The scheduler marks a task run as dispatchable; the
worker then competes for it. Each mechanism therefore protects against a
different failure instead of overlapping the next one:

- the **outbox** prevents a dual write — the "dispatchable" row and the message
  commit together, so a crash between them cannot lose the message;
- **`claim_task_run`** selects exactly one winner, so two workers consuming the
  same message is harmless rather than a double execution;
- the **fencing token** ensures that even a winner from an earlier generation
  cannot write a result a newer owner has superseded.

**Recovery** (Phase 4's "scheduler recovery" item) is the lease reaper: rows
where `lease_expires_at` has passed are returned to `READY`, which makes them
eligible again. It uses the existing partial index. Re-dispatch after a reaper
pass must carry the same dispatch identity rather than a new one, so a redelivery
is recognised instead of looking like a fresh task.

**The outbox is required regardless of the delivery mechanism.** It is not a
feature of the custom-scheduler choice; it is the price of writing to PostgreSQL
and publishing to RabbitMQ without a dual write. What changes is only who
publishes.

## Rationale

**Two overlapping at-least-once systems is worse than one.** Celery's
`acks_late`, prefetch, ETA retries and visibility timeouts are a competing — and
looser — notion of "exactly one worker". FlowForge's engine already answers the
same question, better, with a conditional `UPDATE`. Running both invites the
failure mode where the less correct one appears to win: a Celery redelivery makes
work look duplicated while the database claim correctly refused it, and an
operator debugging that has to reason about two systems that each think they are
in charge.

**Celery contributes nothing to the property FlowForge is built to sell.** The
transitions repository is already broker-agnostic. Fencing, leases and the
state tables survive any delivery mechanism, and none of them need a task
decorator.

**The scheduler loop is custom either way.** "Poll PostgreSQL for eligible
tasks" cannot be expressed as a Celery beat interval without being a custom loop
in a Celery task's clothing. If the loop is ours, Celery adds nothing there.

**The remaining work Celery would save is Phase 5 work.** The worker process —
consume, execute a handler, heartbeat, renew the lease, ack or nack — does not
exist in any design, and writing it is the honest Phase 5 scope. Celery would
only make a difference if the plan were to *use* Celery's worker instead of
writing one, which is the alternative addressed below.

**The interim purpose is served.** Phase 0 needed something wired so Compose, CI
and the smoke test had something to exercise. All three exist, and none of them
require Celery: the health probes, the one-shot `migrate` gate and the compose
smoke test are independent of the worker implementation. Keeping a broker client
that is slated for removal would carry an interest payment — five pinned
packages, an ongoing bump stream, and a config surface a contributor must learn —
through the phase that decides correctness.

## Consequences

- `pyproject.toml` loses `celery`, and with it `kombu`, `billiard` and `vine`;
  the `redis<6` pin that exists only because Celery 5.4 declares it can be
  revisited (it is *not* in this ADR's scope — the cache uses Redis directly).
- `compose.yaml` replaces `celery -A flowforge beat` and `celery -A flowforge
  worker` with FlowForge's own `scheduler` (and, from Phase 5, `worker`)
  processes. The service names stay, so the deployment shape is unchanged.
- `flowforge/celery.py` and the three lines in `flowforge/__init__.py` go. The
  domain import-ban test already lists `celery`, so it stays valid.
- **The CI compose smoke test must change and must be updated in the same PR.**
  It currently boots the Celery services; after this it boots FlowForge's. A
  smoke test that still probes Celery would pass while testing the thing it was
  replacing — the exact failure mode that made Codecov silently green for two
  phases.
- **Nothing executes a task end-to-end until Phase 5.** This is not a
  regression: Phase 3 already leaves a triggered run in `pending`. The
  difference is that the gap is now visible in the process list as well as in the
  database.
- Phase 5 must deliver the worker process, lease renewal, heartbeats and
  handler dispatch. That scope was always in the roadmap; it is now the only
  path to execution rather than an alternative to it.
- ADR-007's interim status ends. It is not deleted: it explains why Celery was
  adopted and what it bought, which is the record of a decision that was correct
  at the time.

## Alternatives Considered

**Keep Celery; layer the outbox, leases and fencing tokens onto its worker model.**
The layering works — a Celery task would take a `task_run_id`, call
`claim_task_run`, and call `finish_task_run` with its token. Reason to reject it:
it is the "two at-least-once systems" case. It also keeps Celery's delivery
semantics in charge of the one thing the fencing token exists to defend against
(a worker that woke up late believing it still owns the row), and it preserves a
dependency tree whose only remaining purpose is to be replaced.

**Keep Celery for transport; write a custom scheduler loop beside it.**
Cheapest immediate change, and the most confusing result. Two systems would
decide, independently, when work happens. The scheduler would dispatch through a
transport whose own retry model can re-present work the claim has already
correctly refused, and the operator would have to know both to read a delay.

**Publish directly from the scheduler without an outbox.**
Fastest to write and disqualified by construction: it reintroduces the dual write
this architecture explicitly exists to avoid. A crash between the dispatch update
and the publish loses the message silently, and no amount of care in the publish
call fixes a commit that has already happened.

## What Phase 4 Must Deliver for This Decision to Hold

- Scheduler loop over `ready_tasks`, writing an outbox row in the same
  transaction as the eligibility work.
- Publisher with `SELECT … FOR UPDATE SKIP LOCKED`, publisher confirms, and a
  dispatch mark that survives a crash between publish and mark — the redelivery
  must be recognised, not duplicated.
- Lease reaper using the existing partial index, with a test proving a reaped
  lease is reclaimed and that a resumed worker from an earlier generation cannot
  write a result.
- Migration for the outbox table, and the compose/CI updates listed above.
