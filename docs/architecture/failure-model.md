# Failure model

FlowForge is a distributed system with four moving parts: PostgreSQL, RabbitMQ,
and three processes (scheduler, publisher, worker) that share nothing but the
database and the broker. "It works" therefore means something narrower than usual:
*every failure mode either loses nothing or is detected and reported.* This
document enumerates those modes, names the mechanism that covers each one, and
points at the test that proves it.

**A mode without a named test is marked UNVERIFIED.** That is deliberate. A
document that implies coverage it does not have is worse than one that admits a
gap, because the gap then gets fixed.

## How to read this

Each entry answers three questions:

1. **What breaks** — the fault, stated concretely.
2. **What could go wrong** — the specific damage if nothing handled it (a lost
   task, a double execution, a stuck run).
3. **What covers it** — the mechanism, and the test that demonstrates it.

Test names are given without their file path when unambiguous; the file is named
when the same test name exists in both `tests/unit` and `tests/integration`.

## Summary

| # | Failure mode | Covered by | Status |
|---|---|---|---|
| 1 | Concurrent transitions | Conditional `UPDATE ... WHERE state = ?`; fencing token | Verified |
| 2 | Duplicate delivery | `claim_task_run` matches only `READY` | Verified |
| 3 | Worker crash mid-task | Lease expiry → reaper → retry | Verified |
| 4 | Scheduler crash mid-pass | One transaction per run; unique outbox row | Verified |
| 5 | Broker outage | Transactional outbox; row stays pending | Verified |
| 6 | Transaction boundaries | Single `atomic()` around eligibility + outbox | Verified |
| 7 | Lease expiration | `reap_expired_leases` / `reclaim_task_run` | Verified |
| 8 | Retry exhaustion | `DEAD` state + `requeue_dead` | Verified |
| 9 | Lost acknowledgement | Row stamped only after the broker confirms | Verified |
| 10 | Stale results | Fencing token on `finish_task_run` | Verified |
| 11 | Partial failure of a run | `derive_workflow_run_state` precedence | Partially verified |
| 12 | Graceful shutdown | Manual ack + `stop()` after in-flight work | Verified |
| 13 | Recovery after restart | Django reconnects; outbox drains | Verified |
| 14 | Broker reconnection | Worker and publisher reconnect with backoff, no restart | Verified (v0.7.1) |
| 15 | Non-idempotent handler replay | `idempotency_key` (opt-in per task) | Partially verified |
| 16 | Clock skew across processes | Single-process `now()` | UNVERIFIED |
| 17 | Broker loses a confirmed message | Quorum queue fsyncs before confirming | Verified for abrupt termination, single node (v0.7.1) |

## 1. Concurrent transitions

**What breaks.** Two schedulers, two workers, or a worker and the reaper write
the same row at the same time.

**What could go wrong.** Both read `READY`, both dispatch, the task runs twice;
or a late writer overwrites a newer outcome.

**What covers it.** Every state change is one conditional statement:
`UPDATE ... WHERE id = ? AND state = ?`. The database decides the winner; no
read-then-write window exists. Losing the race returns `False` — it is not an
error, because the caller was merely late. Illegal transitions raise *before* any
SQL runs, so a caller bug cannot be mistaken for contention.

**Tests.** `test_exactly_one_of_many_claims_wins`,
`test_exactly_one_of_many_run_starts_wins`,
`test_only_one_writer_can_finish_with_the_same_token`,
`test_exactly_one_reaper_reclaims_an_expired_lease`,
`test_reap_expired_leases_is_safe_under_concurrency`,
`test_a_stale_token_loses_even_under_concurrency`,
`test_the_winner_is_the_worker_recorded_on_the_row`.

All are PostgreSQL-only and run in CI with `REQUIRE_POSTGRES=1`. They are
meaningless on SQLite, which serialises writers, so they **skip** locally rather
than pretend.

## 2. Duplicate delivery

**What breaks.** RabbitMQ delivers a message twice — because the ack was lost, or
because the consumer's channel closed after the handler ran but before the ack.

**What could go wrong.** The task executes twice, duplicating its side effect.

**What covers it.** `claim_task_run` only matches a `READY` row. A redelivered
message for an already-claimed or already-finished task matches zero rows and is
acked without executing. At-least-once delivery, at-most-once execution.

**Tests.** `test_duplicate_delivery_runs_the_handler_once` (unit **and**
integration — the integration one uses a real broker redelivery),
`test_lost_ack_redelivers_the_message`.

**Not covered.** A handler whose side effect is not idempotent and which crashes
*after* performing it will replay that effect. See #15.

## 3. Worker crash mid-task

**What breaks.** `SIGKILL` to a worker while its handler is running. No cleanup
code runs; the row is left `RUNNING` with a lease that nothing renews.

**What could go wrong.** The task is stuck `RUNNING` forever, and the run never
finishes.

**What covers it.** The lease expires on its own clock. The scheduler's reaper
(`reap_expired_leases`) finds `RUNNING` rows whose `lease_expires_at` has passed
and routes them by retry policy: `RETRYING` with a backoff while attempts remain,
`DEAD` once exhausted. Either way the fencing token is bumped, so the dead
worker's eventual write matches zero rows.

**Tests.** `test_a_killed_worker_is_reaped_retried_and_finally_succeeds` — starts
`manage.py worker` as a subprocess against real PostgreSQL and RabbitMQ, waits
until the task is `RUNNING`, `SIGKILL`s the process, asserts the row is still
`RUNNING`, then asserts the reaper consumes the attempt (`attempt == 2`, token
bumped, lease cleared) and that a second worker finishes the task. No arbitrary
sleeps: the reaper is polled until the lease lapses.

## 4. Scheduler crash mid-pass

**What breaks.** The scheduler is killed while advancing a run.

**What could go wrong.** A task is promoted to `READY` but no outbox row is
written (lost dispatch), or an outbox row is written but the run was never started
(a run that never runs).

**What covers it.** `advance_workflow_run` performs the eligibility change and the
outbox write in **one** transaction, and `OutboxMessage` is unique on
`(task_run, fencing_token_snapshot)`. Killing the process between the two facts is
impossible; killing it after the commit leaves a state a fresh pass resumes from
without duplicating anything.

**Tests.** `test_a_killed_scheduler_cannot_double_dispatch` — runs
`manage.py scheduler` as a subprocess, waits for the outbox row, `SIGKILL`s it,
then runs a fresh pass and asserts the row count is unchanged.
`test_repeated_passes_do_not_duplicate_dispatches` and
`test_enqueue_ready_tasks_is_idempotent_per_generation` cover the same property
without a crash.

## 5. Broker outage

**What breaks.** RabbitMQ is stopped, unreachable, or refuses the publish.

**What could go wrong.** The scheduler believes it dispatched a task that never
reached a queue, so the task is never executed and nothing retries it.

**What covers it.** The transactional outbox. The scheduler never talks to the
broker; it writes a row. `publish_once` publishes inside one transaction and
stamps the row only after the broker confirms, so a failed publish rolls back and
leaves the row pending for the next sweep. Publishing is `mandatory=True`, so an
unroutable message raises instead of being dropped into the void.

**Tests.** `test_outage_leaves_rows_pending_then_recovery_drains_them_once`,
`test_unroutable_publish_leaves_the_row_pending`,
`test_a_committed_row_is_delivered_by_a_fresh_publisher`,
`test_two_publishers_publish_each_row_exactly_once`.

**Container-level proof.** The `docker` CI job stops the `rabbitmq` container and
asserts `/health/ready/` returns **503** with `"rabbitmq": "error"`, then starts
it again and asserts 200. A readiness probe that stays green while the broker is
down is worse than no probe.

## 6. Transaction boundaries

**What breaks.** A crash between two writes that must be atomic.

**What could go wrong.** A dual write: the database says "dispatch me" and the
broker never hears about it, or vice versa.

**What covers it.** Three boundaries are explicit and tested:

- eligibility change + outbox row → one transaction (`advance_workflow_run`);
- outbox fetch + publish + stamp → one transaction (`publish_once`), with
  `FOR UPDATE SKIP LOCKED` so two publishers divide the queue without blocking;
- claim → one statement, which is what makes the fencing token unforgeable.

**Tests.** `test_two_publishers_divide_the_queue_without_overlap`,
`test_a_killed_scheduler_cannot_double_dispatch`,
`test_exactly_one_of_many_claims_wins`.

## 7. Lease expiration

**What breaks.** A worker holds a lease it can no longer renew — because it died,
or because its heartbeat thread lost the race with the reaper.

**What could go wrong.** A live worker and a reaper both believe they own the
task.

**What covers it.** `renew_lease` and `finish_task_run` both require the current
fencing token and `state = RUNNING`. Once the reaper bumps the token, the old
worker's heartbeat and finish match zero rows. The heartbeat stops when it loses
the lease, so it does not spin.

**Tests.** `test_reap_routes_an_expired_lease_into_retrying`,
`test_a_live_lease_is_not_reaped`,
`test_a_resurrected_worker_cannot_write_after_reap`,
`test_renew_lease_refuses_a_stale_token`,
`test_heartbeat_renews_the_lease_while_the_handler_runs`.

## 8. Retry exhaustion

**What breaks.** A task fails every attempt its policy allows.

**What could go wrong.** Either the task is retried forever (a poison message that
consumes the whole queue), or it silently disappears.

**What covers it.** The policy is per task (`config["retry"]`, default 3 attempts
with exponential backoff and full jitter). `schedule_retries` moves `FAILED` to
`RETRYING` while attempts remain and **buries** it in `DEAD` when they do not.
`DEAD` is terminal: nothing retries it automatically, and `manage.py requeue_dead`
is the documented, audited way back.

**Tests.** `test_a_failed_task_is_scheduled_for_retry_with_backoff`,
`test_a_failed_task_is_buried_once_the_policy_is_exhausted`,
`test_reap_buries_the_task_once_the_policy_is_exhausted`,
`test_due_retries_are_promoted_back_to_ready`,
`test_requeue_dead_returns_the_task_to_pending`,
`test_requeue_dead_refuses_a_task_that_is_not_dead`,
`test_requeue_dead_rejects_a_malformed_id`,
`test_requeue_dead_reports_an_unknown_id`.

## 9. Lost acknowledgement

**What breaks.** The broker receives the message, but the publisher never learns
that it did — the confirm is lost.

**What could go wrong.** If the row were stamped optimistically, the message would
be considered delivered and never republished: at-most-once, and a lost task.

**What covers it.** The row is stamped only *after* the confirm. A lost confirm
therefore leaves it pending and a later sweep publishes it again — at-least-once.
The duplicate is harmless because of #2.

**Tests.** `test_a_lost_publisher_confirm_keeps_the_outbox_row_pending` (real
RabbitMQ: the message is genuinely delivered, then the confirm is discarded; the
row must still be pending, and the next sweep must deliver and stamp it exactly
once), `test_lost_ack_redelivers_the_message`.

## 10. Stale results

**What breaks.** A worker that was presumed dead wakes up and writes its result.

**What could go wrong.** The stale result overwrites the newer attempt's outcome,
so the database records a success that did not happen last.

**What covers it.** `finish_task_run` requires `fencing_token = <the claim's
token>`. Every reaper pass and every claim increments the token, so a resurrected
worker's write matches zero rows and is discarded. The worker then acks (it is
correctly rejected, not an error) rather than retrying forever.

**Tests.** `test_killed_worker_result_is_rejected_after_a_reap`,
`test_a_stale_token_cannot_overwrite_a_newer_attempt`,
`test_stale_finish_is_acked`.

## 11. Partial failure of a run — **partially verified**

**What breaks.** One task in a run fails while its siblings are still running.

**What could go wrong.** The run is reported `RUNNING` while it is already doomed,
so the operator learns about the failure only when the last task finishes — or
worse, the run is reported `SUCCEEDED` because the failed task was not counted.

**What covers it.** `derive_workflow_run_state` encodes the precedence as data:
cancelled beats failed beats succeeded beats running beats pending. A `DEAD` task
counts as a failure. An empty run is `PENDING`, not `SUCCEEDED`.

**Tests.** `test_run_fails_when_a_task_fails`,
`test_run_succeeds_when_all_tasks_succeed`.

**What is not covered.** Failing siblings are **not cancelled**. The run is marked
`FAILED` as soon as the first task fails, but tasks already `RUNNING` run to
completion. That is a deliberate policy (cancelling a running handler requires a
cooperative signal the handler does not implement), but it means "failed" describes
the run, not the absence of further work. Cancellation of in-flight siblings is
UNVERIFIED because it is not implemented.

## 12. Graceful shutdown

**What breaks.** `SIGTERM` to a worker during a rolling deploy.

**What could go wrong.** The in-flight task is abandoned mid-write; the message is
acked but the result was never committed, or the process exits 0 while leaving a
`RUNNING` row with a live lease.

**What covers it.** Manual ack: the message is acked only after
`finish_task_run` commits. `stop()` halts consumption and the in-flight message
finishes first. Anything unacked is redelivered.

**Tests.** `test_sigterm_finishes_the_in_flight_task_and_exits_cleanly` (real
subprocess, real PostgreSQL, real RabbitMQ: asserts the task reaches `SUCCEEDED`,
the lease is cleared, no `RUNNING` row keeps a live lease, the queue drains, and
the process exits **0**), `test_graceful_shutdown_finishes_the_in_flight_message`.

## 13. Recovery after restart

**What breaks.** PostgreSQL or RabbitMQ restarts under a running stack.

**What could go wrong.** Every pooled connection is dead; the first request after
the restart fails, or the stack never recovers.

**What covers it.** Three different shapes of process need three different
answers, and v0.7.1 found that only the first one worked:

- **The web process** opens a connection per request, so it recovers with no code
  at all.
- **The scheduler and publisher** hold one connection for their whole life. A
  restart kills that socket, and every later query raises
  `OperationalError: terminating connection due to administrator command`. Both
  loops now catch `DatabaseError`, discard the connection
  (`core.db.reset_connections`) and back off, instead of dying. Before this fix the
  scheduler **exited**, and every run stayed `pending` — the docker job caught it
  as a 120s timeout.
- **The worker** catches `DatabaseError` per message, discards the connection and
  re-raises, so the message stays unacked, the broker requeues it, and the
  reconnect loop applies the backoff. The lease covers a half-written attempt.

**Tests.** The `docker` CI job restarts the `db` container and asserts
`/health/ready/` returns to 200 with `"postgres": "ok"`, then runs the end-to-end
sample workflow again to prove the pipeline still does real work. Without that
second assertion, "the probe is green" would be the only claim — and a green probe
over a dead scheduler is exactly what this run produced.
`test_a_database_error_resets_the_connection_and_propagates` covers the worker's
half at unit level.

## 14. Broker reconnection — verified in v0.7.1

**What breaks.** RabbitMQ goes away and comes back while the worker and publisher
are running.

**What used to happen.** Both opened their pika connection once. When it dropped,
the worker's `start_consuming()` raised and the process exited; the publisher
failed its next sweep. Nothing was lost — the outbox row stayed pending and the
lease was reaped — but the processes had to be restarted by hand. That was the
v0.7.0 known limitation.

**What happens now.** `Worker.run()` is a loop. A connection-level failure is
backed off exponentially with jitter (capped at 30s) and retried; the connection,
channel, queue declaration and consumer are all re-established. The publisher
retries its sweep on the same curve, and its rows stay pending until the broker
confirms them. Both waits are on a `stop` event, so SIGTERM during an outage exits
promptly instead of waiting out the delay.

**The in-flight message.** If the connection dies between the handler and the ack,
the ack raises. The worker does not swallow it — swallowing would tell the broker a
message is finished when it is not, which is how a task is lost. The connection is
torn down, the broker requeues the message, and the redelivery is deduplicated
against the row that is already terminal.

**Tests.**

- `TestReconnection::test_a_lost_connection_is_retried_then_consumption_resumes`
  (unit) — one failure does not end the process; `basic_consume` runs on the retry.
- `TestReconnection::test_stop_interrupts_a_reconnect_wait` (unit) — with a 30s
  backoff configured, `stop()` still returns immediately.
- `TestReconnection::test_a_queue_type_mismatch_is_not_retried` (unit) — the one
  error that is an operator action, not a transient fault.
- `TestReconnection::test_an_ack_failure_is_not_treated_as_success` (unit) — the
  exception propagates instead of being mistaken for a clean ack.
- `test_a_dropped_connection_redelivers_and_completes_exactly_once` (integration,
  real RabbitMQ) — the connection is closed mid-task through the connection's own
  IOLoop; the task still reaches `SUCCEEDED`, the handler runs **once**, the worker
  reconnects by itself, and a second dispatch completes on the same worker.
- Docker CI, "Broker outage is reported, recovered from, and a queued run
  completes" — a workflow is triggered **while the broker is stopped**, the
  container IDs are compared before and after (a manual restart would change them),
  and the run must reach `SUCCEEDED`. Nothing is restarted by hand.

**What is still not claimed.** Reconnection is per process. A worker that was
killed (not disconnected) still needs its supervisor to restart it — that is a
process-supervision concern, not a connection concern.

## 15. Non-idempotent handler replay — **partially verified**

**What breaks.** A handler performs a side effect (charge a card, send an email)
and the process dies before the result is committed.

**What could go wrong.** At-least-once delivery means the handler runs again and
the side effect happens twice.

**What covers it.** Nothing automatic — and that is the honest answer. A task may
declare `config["idempotency_key"]`; the worker then reuses the persisted result
of an earlier `SUCCEEDED` task run with the same key instead of executing again.

**Tests.** `test_an_idempotent_dispatch_reuses_a_prior_result`,
`test_a_dispatch_without_an_idempotency_key_always_executes`,
`test_a_successful_handler_result_is_persisted`,
`test_a_failed_handler_persists_no_result`.

**What is not covered.** The lookup is best-effort, not a lock: two concurrent
executions of the same key can both run. Request-level de-duplication is the API's
`Idempotency-Key` header, which is a different guarantee. A handler without a key
that is not naturally idempotent **will** replay. This is documented rather than
solved, and it is why the registry is trusted-only: the operator decides which
handlers are safe to retry.

## 16. Clock skew across processes — UNVERIFIED

**What breaks.** The scheduler's clock differs from the database's, or from the
worker's.

**What could go wrong.** `lease_expires_at < now` is evaluated with the
*scheduler's* `now`, so a fast scheduler clock could reap a lease that is still
live from the worker's point of view. The fencing token keeps that safe (the
worker's write is rejected), but the task is retried unnecessarily. A slow clock
delays reaping, extending the time a crashed task stays `RUNNING`.

**Why it is unverified.** Every timestamp is produced by the application process
(`timezone.now()`), never by the database, so there is no cross-clock comparison
to test. The safety property (no double execution) is covered by the fencing
token; the liveness property (a lease is reaped promptly) is not tested under
skew, and there is no clock-skew injection harness.

**Mitigation, not a fix.** Leases are 30s by default against a heartbeat interval
of 10s, so skew must exceed roughly 20s before it matters. NTP is assumed.

## 17. Broker loses a confirmed message — verified for abrupt termination, single node

**What breaks.** RabbitMQ confirms a publish, then dies before the message is
durably on disk.

**What could go wrong.** The outbox row is stamped `published_at`, so it is never
republished, and the message is gone: a lost task.

**What covers it.** The dispatch queue is a **quorum queue** (`x-queue-type:
quorum`). A quorum queue writes through a Raft log and fsyncs before acknowledging,
so a confirm means the message is on disk — not merely in a memory buffer. This is
the difference from a classic durable queue, which can acknowledge a message it has
not flushed.

**Test.** Docker CI, "A confirmed dispatch survives an abrupt broker kill". The
worker is stopped so a dispatch stays parked in the queue, the outbox is polled
until every row is confirmed, then the broker is **SIGKILLed** — not stopped, so
there is no graceful flush — restarted, the worker restarted, and the run must
reach `SUCCEEDED`. If the message were only in memory, the kill would lose it.

**What is still not claimed.**

- **Single node only.** There is no replica, so this is crash-safe durability, not
  high availability. A three-node cluster would be needed for failover, and nothing
  here tests that.
- **Not tested against disk loss or corruption.** fsync protects against process
  death, not against a failing volume.
- **The queue type cannot be changed in place.** Upgrading an existing classic
  queue needs the delete-and-redeclare procedure in
  [`execution-model.md`](execution-model.md#the-dispatch-queue-is-a-quorum-queue);
  `declare_dispatch_queue` raises `QueueTypeMismatchError` with the steps rather
  than failing opaquely.

## What this document does not claim

- It does not claim the system is correct. A green CI run is evidence that one
  interleaving worked, not that all of them do.
- It does not claim the tests above are exhaustive. They are the tests that exist,
  named so a reader can go and read them.
- It does not treat "UNVERIFIED" as a synonym for "fine". Each unverified entry
  names the risk it carries and, where one exists, the fix.
