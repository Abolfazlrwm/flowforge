# ADR-012: Retry Policy and Dead-Letter Semantics

**Date:** 2026-10-21
**Status:** Accepted — scope mandated by the owner's Phase 6 directive
(2026-10-21); implementation choices below are recorded as such, not attributed to
the owner
**Related:** Phase 6 — Reliability (v0.7.0); [ADR-011](ADR-011-custom-scheduler-and-transactional-outbox.md)
(scheduler, outbox, lease reaper)

> **What the owner decided, and what this ADR decided.** The Phase 6 directive
> specified the *scope*: "retry policy per task (max attempts, exponential backoff
> with jitter), retrying/failed semantics", "dead-letter queue + DEAD handling with
> manual requeue". It did not specify attempt numbering, the default budget, whether
> a dead worker consumes an attempt, or whether `FAILED` remains non-terminal.
> Those are recorded here as implementation choices with their reasoning, so they
> can be changed deliberately rather than discovered.

## Context

Phase 5 left two states that could not answer a simple operational question.

`FAILED` was **non-terminal**, but nothing moved a task out of it. The machine
declared `schedule_retry` (FAILED→RETRYING) and `retry_ready` (RETRYING→READY),
and no code called either. A task that failed stayed `FAILED` forever, its run
went `FAILED`, and the only recovery was to trigger a new run — which re-executes
every task, including the ones that had already succeeded.

`reap_lease` (RUNNING→READY) had the opposite problem: it was *too* forgiving. A
worker that died was returned to `READY` with no record that an attempt had been
consumed. A task that reliably crashed its worker — a poison message, an OOM, a
segfault in a native dependency — would be dispatched, kill its worker, be reaped,
and be dispatched again. Forever. The fencing token made each generation safe
individually, but nothing bounded the number of generations, so the loop was
invisible: no error, no alert, just a task that never finishes and a worker that
keeps dying.

The question Phase 6 has to answer is therefore not "should there be retries" but
**"what counts as an attempt, and when does retrying stop?"**

## Decision

### 1. `DEAD` is terminal; `FAILED` is not

`TaskRunState.DEAD` is added, and the terminal set becomes
`{SUCCEEDED, DEAD, CANCELLED}`. `FAILED` remains non-terminal because it still has
a way out — a retry — and calling it terminal would have made the retry edges
unreachable.

`DEAD` means "the policy is exhausted; a human must decide". Nothing moves a task
out of `DEAD` automatically. `manage.py requeue_dead` is the way back, and it is
deliberate, audited, and refuses anything that is not `DEAD`.

### 2. A dead worker consumes an attempt

`reap_lease` (RUNNING→READY) is removed. The reaper now routes by policy:

- `attempt < max_attempts` → `reap_retry` (RUNNING→**RETRYING**) with
  `scheduled_at = now + backoff` and the attempt counter incremented;
- otherwise → `reap_dead` (RUNNING→**DEAD**).

This is the decision that closes the poison-message loop. A worker that dies is a
failed attempt — it is not a free retry — so `max_attempts` bounds total
executions, not just handler exceptions.

**Consequence, stated plainly:** a task that crashes its worker now dies after
`max_attempts` (3 by default) instead of looping forever. That is the intended
trade: three failed attempts and a visible `DEAD` row beat an invisible loop.

### 3. `attempt` is 1-based and means "the attempt in progress"

`TaskRun.attempt` defaults to `1`, not `0`. The column answers "which attempt is
this row on", not "how many attempts have completed". A retry increments it, so a
row in `RETRYING` with `attempt = 2` means "the second attempt is pending".

This matches the pre-existing column (it already defaulted to 1) and avoids
rewriting every existing row, but it is the reason `reclaim_task_run` compares
`attempt < max_attempts` *before* incrementing.

### 4. The backoff lives in `scheduled_at`, not in the dispatcher

`RETRYING` carries `scheduled_at`, enforced by the CHECK constraint
`taskrun_scheduled_iff_retrying` (non-null exactly while the row is `RETRYING`).
`promote_due_retries` flips `RETRYING → READY` only once `scheduled_at <= now`.

The alternative — sleeping in the scheduler until the retry is due — was rejected:
it makes the scheduler's pass length depend on the longest backoff, and a
scheduler restart would lose the sleep. A column survives a restart.

### 5. Backoff is exponential with a cap and optional full jitter

`min(base * 2**(attempt-1), max)`, multiplied by `uniform(0, 1)` when jitter is
enabled. Jitter is on by default.

Full jitter rather than equal jitter or decorrelated jitter: the goal is to spread
a herd of failures, and `uniform(0, raw)` does that with the least arithmetic. The
cost is that a retry can fire almost immediately, which is acceptable because the
scheduler's pass interval (5s) dominates.

### 6. The policy is per task, read from `config["retry"]`

A task that says nothing gets 3 attempts, 1s base, 60s cap, jitter on. A task may
override any key. The policy is read at the moment it is needed (in the reaper and
in `schedule_retries`), not stored on the row, so changing a definition changes
retry behaviour for runs already in flight. That is a deliberate choice for
operability, and it is a real semantic: a task's retry policy is *not* a snapshot
of its definition at trigger time.

### 7. Retries reuse the row

`RETRYING` does not create a new `TaskRun`. The unique `(workflow_run, task)`
constraint stays, the attempt counter distinguishes generations, and
`started_at` is preserved by `COALESCE` so "time to first start" stays meaningful.
Per-attempt history is therefore *not* stored — only the current attempt and its
outcome. A per-attempt audit table is the obvious extension and is not in v0.7.0.

### 8. `FAILED` is still reported as a run failure

`derive_workflow_run_state` returns `FAILED` for a run with any `FAILED` **or**
`DEAD` task. A run does not stay `RUNNING` while a retry is pending: the operator
learns early. `RETRYING` counts as "in flight" for the purpose of not being
`PENDING`, but it cannot outvote a failure.

## Consequences

**Easier**

- A crash loop is bounded and visible. `DEAD` is a state an operator can query.
- Retry behaviour is tunable per task without a deploy.
- The scheduler stays a stateless loop: backoff is data, not a sleep.
- A redelivered or re-dispatched task is safe because the row, the token and the
  attempt counter all move together.

**Harder**

- `reap_lease` no longer exists, so anything that assumed "a reaped task is
  immediately READY again" is wrong. The scheduler's `schedule_once` had to grow a
  `promote_due_retries` step, and every test that asserted the old behaviour had
  to be rewritten (six of them).
- `FAILED` being non-terminal means a run can be `FAILED` while a task is still
  `RETRYING`. That is a genuine inconsistency in the *reporting* model: the run
  says "failed", the task says "still trying". It is deliberate (fail fast,
  recover later), but it means the run state is not a summary of its tasks in the
  way it was before.
- The `scheduled_at` CHECK constraint makes the column's lifecycle part of the
  schema. `transition_task_run` must clear it on every non-`RETRYING` target; the
  first implementation did not, and the retry tests caught it (see CHANGELOG).

**Now impossible without a new ADR**

- Retrying a `DEAD` task automatically.
- Returning a reaped lease straight to `READY`.
- Storing a different retry policy per attempt.

## Alternatives rejected

**Keep `reap_lease` and bound retries with a separate counter.** Rejected: two
counters that must agree is one counter too many, and the reaper's job is already
"decide what happens to this dead attempt" — the decision belongs in one place.

**Make `FAILED` terminal and add a `RETRYING`-only path from the worker.** Rejected:
the worker would have to know the policy, which means the policy is duplicated in
the executor and the scheduler, and a worker crash could never schedule a retry
because no worker is left to do it.

**Sleep until the backoff elapses inside `schedule_once`.** Rejected: it couples
the scheduler's pass length to the longest backoff, and a restart loses the wait.
A `scheduled_at` column is durable by construction.

**A separate dead-letter queue in RabbitMQ (`x-dead-letter-exchange`).** Rejected
for v0.7.0: the durable state is PostgreSQL, and a broker-side DLQ would be a
second place where "dead" is recorded, with no transaction spanning the two.
`DEAD` as a task state is queryable, joinable to its run, and survives a broker
wipe. A broker DLQ remains useful for *unparseable* messages, which the worker
already drops with `requeue=False`.

**Equal jitter (`raw/2 + uniform(0, raw/2)`).** Rejected: it guarantees a minimum
delay, which sounds better but makes the first retry of a fast-failing task wait
`base/2` for no reason. Full jitter's early retries are harmless at a 5s pass
interval.

## What this ADR does not claim

- It does not claim retries make handlers safe. At-least-once delivery means a
  handler can run twice; `idempotency_key` is opt-in and best-effort, not a lock.
  See failure modes 2 and 15 in
  [`docs/architecture/failure-model.md`](../architecture/failure-model.md).
- It does not claim `max_attempts` bounds *time*. A task with a 60s cap and 10
  attempts can occupy its run for over an hour; the policy bounds executions, not
  duration. A wall-clock deadline is not implemented.
