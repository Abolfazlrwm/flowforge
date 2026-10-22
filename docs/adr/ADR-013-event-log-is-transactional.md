# ADR-013: The Event Log Is Transactional With Its State Change

**Date:** 2026-10-22
**Status:** Accepted
**Related:** Phase 7 — Advanced Workflows (v0.8.0); the `TaskRunEvent` append-only
event log; [ADR-011](ADR-011-custom-scheduler-and-transactional-outbox.md) (the
outbox is already transactional with its state write, for the same reason)

## Context

`TaskRunEvent` records every task-run transition as an append-only audit trail,
emitted from `core/repositories/transitions.py` beside each conditional `UPDATE`.
The transition functions are not uniform in how they transact:

- `claim_task_run`, `reclaim_task_run`, `requeue_dead_task_run`,
  `requeue_failed_task_run`, and every `transition_task_run` call site (the
  scheduler's `advance_workflow_run`, `schedule_retries`, `promote_due_retries`)
  already run the state write and the event insert inside one `transaction.atomic()`.
- `finish_task_run` and `cancel_task_run` perform a bare conditional `UPDATE` in
  autocommit, then insert the event as a *separate* statement.

That last case is a **best-effort** event: if the process dies between the state
`UPDATE` committing and the event `INSERT` committing, the state change is durable
but its event is lost, and the audit trail silently has a gap.

## Decision

Make the event insert part of the **same transaction** as the state change, for
every path. Concretely, wrap `finish_task_run` and `cancel_task_run` in
`transaction.atomic()` so their `UPDATE` and event insert commit or roll back
together; the other paths already do.

The consequence is that an audit trail entry never exists without its state change,
and a state change never commits without its audit entry. A crash rolls back both.

## Alternatives considered

- **Leave it best-effort and document the gap.** Rejected: the whole value of an
  audit trail is completeness and ordering, and a gap is indistinguishable from
  "the transition never happened". The fix is a two-line `atomic()` wrapper, so the
  cost of closing the gap is negligible.
- **A separate audit service / different table.** Rejected: over-engineered for a
  row insert that already sits next to the write it describes.

## Consequences

- `finish_task_run` and `cancel_task_run` now hold their row lock until the
  transaction commits (the event insert is inside it). This does not change the
  outcome of the cancel-vs-completion race — the conditional `UPDATE` still decides
  the single winner, and the loser's write matches zero rows — it only holds the
  lock a fraction longer. The concurrency tests pin this.
- The best-effort caveat is removed from the event-log documentation; the
  remaining durability property is exactly "the event and the state commit
  together", which is the same guarantee the outbox already gives its dispatch
  rows (ADR-011).
