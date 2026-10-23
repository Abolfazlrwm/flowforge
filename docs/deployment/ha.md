# High availability

This document describes how FlowForge behaves under component failure and how to
make a deployment highly available. It is deliberately honest about the
difference between **what the code guarantees**, **what the shipped defaults
give you**, and **what has actually been exercised in CI**. The three are not the
same, and conflating them is how an operator gets surprised.

The short version:

- All four **application processes are stateless and scale horizontally.** The
  database — not a leader-election protocol — is the mutex that keeps concurrent
  instances from doing the same work twice. This is a property of the code, not a
  deployment nicety.
- The **stateful components** (PostgreSQL, RabbitMQ, Redis) are the failure
  domains. Their availability is a property of *how you run them*, and the
  shipped defaults run each as a **single instance**.

## Application processes: stateless, scale freely

| Process | Role | Scale out? | Why it is safe |
|---------|------|-----------|----------------|
| `web` | REST API | Yes, freely | Stateless request/response. Authentication is a token in the `Authorization` header — no server-side session, so no session affinity is needed. Put N replicas behind a load balancer. |
| `scheduler` | Decides which tasks dispatch | Yes, freely | Two safeguards make concurrent schedulers safe (see below). |
| `publisher` | Drains the outbox to the broker | Yes, freely | Concurrent publishers divide the queue (see below). |
| `worker` | Executes tasks | Yes, freely | Competing consumers on a shared queue; execution is made idempotent (see below). |

### Why the scheduler scales

`enqueue_ready_tasks` locks the `READY` task-run rows with `SELECT … FOR UPDATE`
before creating their dispatch, and `advance_workflow_run` locks the run row
itself (`WorkflowRun.objects.select_for_update()`). Two schedulers advancing the
same run serialize on the row lock; only one creates each outbox row, and
`create_dispatch`'s unique `(task_run, fencing_token_snapshot)` constraint is the
final backstop. Scheduled (cron) triggers are claimed with
`SELECT … FOR UPDATE SKIP LOCKED`, so N schedulers race for each due trigger and
exactly one fires it — the clock is advanced inside the same transaction, so
recovery from a crash is a missing run, never a double-fire.

There is **no leader election and no singleton requirement.** Adding scheduler
replicas increases scheduling throughput and removes the scheduler as a single
point of failure, at the cost of each replica doing a pass over the active-run
set (the redundant passes are cheap no-ops once the row is locked).

### Why the publisher scales

`fetch_pending` reads undelivered outbox rows with
`SELECT … FOR UPDATE SKIP LOCKED`, so two publishers take disjoint batches
without blocking each other; a crashed publisher's rows are simply not locked
and become available to the next sweep. `mark_published` is a conditional
`UPDATE … WHERE published_at IS NULL` that returns `True` for exactly one
caller, and the worker de-duplicates on `dispatch_id` regardless. At-least-once
delivery with idempotent execution is the design; more publishers only adds
delivery throughput.

### Why the worker scales

Workers are competing consumers on one RabbitMQ queue (a quorum queue — see
below). Each message is claimed by exactly one consumer via basic QoS prefetch.
Execution is made safe under at-least-once redelivery by two mechanisms: a
**fencing token** that a reaped lease bumps, so a zombie worker's write matches
zero rows, and an **idempotency key** that lets a worker reuse an existing
`SUCCEEDED` result instead of re-running the handler. Add workers to raise task
throughput; the broker distributes.

## Stateful components

### PostgreSQL — the system of record and the critical SPOF

The application uses a **single `DATABASE_URL`** with no read/write splitting.
Every process writes to one primary. That primary is therefore the component
whose loss stops the system:

- Lose the primary and **writes and reads both stop** until it returns or a
  replica is promoted. There is no second writable endpoint to fail over to
  inside the application.

To make PostgreSQL highly available you run it with a failover path and a backup:

- **Managed PostgreSQL** (Cloud SQL, RDS, Azure Database for PostgreSQL) is the
  recommended route. It gives you a synchronous or asynchronous standby with
  automated failover, plus PITR from WAL archiving. This is an operational choice
  FlowForge does not make for you — it only needs *a* PostgreSQL that is
  reachable at `DATABASE_URL`.
- **Self-managed**: streaming replication to one or more standbys with an
  automated promote (Patroni, repmgr, or pg_auto_failover), plus WAL archiving
  for point-in-time recovery. See `backup-restore.md` for the dump and PITR
  procedure.

The application tolerates a **restart** of the primary without a process restart
— the scheduler, publisher and worker discard their dead connection, back off,
and reconnect (this is exercised by the failure-injection steps in the `docker`
CI job). What it does **not** do is survive the loss of the primary itself: that
needs a standby to promote, which is infrastructure, not application code.

### RabbitMQ — durable dispatch, and a single-node caveat

Dispatches live in a **quorum queue** (`x-queue-type=quorum`), which replicates
each message across queue members through Raft and confirms only after an
`fsync`. A confirmed message survives an abrupt `SIGKILL` of the broker — proven
in CI, which kills the broker with no graceful shutdown and requires the message
to still be there.

The caveat is topology, not message durability:

- **Single node (the shipped default).** The quorum queue has one member. Its
  data survives a *restart* of that node, but if the node is lost and does not
  come back, the queue is gone with it. There is no other member to serve it.
- **Three-node cluster (recommended for HA).** The quorum queue is replicated
  across all three members, so it survives the loss of a single node with no
  data loss and no downtime. Run RabbitMQ as a 3-node cluster behind the app,
  with the queue declared on all three.

Back up the broker's **definitions** (queues, exchanges, bindings, users, vhost,
permissions) — see `backup-restore.md`. Messages are not backed up; in-flight
work is recovered from the PostgreSQL outbox by the publisher, which is the whole
point of the transactional outbox.

### Redis — cache and throttle counters only

Redis is the Django cache backend. Its only two uses are:

1. the readiness probe, which writes and deletes a `__health_check__` key to
   confirm the cache is reachable, and
2. DRF throttle counters (`ScopedRateThrottle`). With a shared Redis these
   counters are cluster-wide; without one they are per-process.

**Redis is not the system of record.** No workflow, run, task, key, or audit row
lives there. If Redis is lost:

- `/health/ready/` reports `503` and the web pods drop out of the load
  balancer (correctly — a readiness probe must name the dependency that is down),
- throttle counters reset or become per-process (the limits are a policy, not a
  guarantee),
- **no workflow data is affected.**

For HA, run Redis with replication and automatic failover (Sentinel or Cluster),
or accept it as a best-effort dependency. Do not treat a Redis outage as a data
loss.

## What the shipped Helm chart gives you

`deployment/kubernetes/helm/flowforge` ships sensible HA *primitives* for the
application processes:

- `web` is a Deployment — set `web.replicas` and put a Service in front.
- `worker` ships with a HorizontalPodAutoscaler and a PodDisruptionBudget.
- `scheduler` and `publisher` are Deployments you can scale out freely, per the
  guarantees above.
- every container runs non-root with a read-only root filesystem and a
  `manage.py healthcheck` exec probe.

The chart does **not** deploy PostgreSQL, RabbitMQ, or Redis — they are expected
to be provided by the platform or external operators. That is deliberate: those
three are exactly the components whose availability model is an infrastructure
decision, not something a chart can responsibly default. Wire them to managed or
clustered instances via `values.yaml`.

## What is and is not verified

**Exercised in CI (the `docker` job):**
- the four processes recover from a broker restart and a PostgreSQL restart
  without a process restart (`tests/integration/test_failure_injection.py` plus
  the container-level failure-injection steps);
- a confirmed dispatch survives an abrupt `SIGKILL` of the broker (quorum-queue
  durability);
- the scheduler, publisher and worker are safe under concurrent access to
  PostgreSQL — the publish races and transition races are PostgreSQL concurrency
  tests that run in CI.

**Safe by construction but not run as a multi-replica deployment in CI:**
- running several `scheduler`, `publisher` or `worker` replicas at once. The
  `FOR UPDATE` / `SKIP LOCKED` guarantees are the same ones the concurrency tests
  exercise, but a genuinely multi-pod deployment on a cluster is not stood up in
  the standard CI gate. The kind-based end-to-end smoke test
  (`scripts/kind-e2e.sh`) runs single-replica.

**Not verified — operator territory:**
- PostgreSQL streaming-replication failover and PITR restore to a chosen
  timestamp. Validate on a clone before you rely on it.
- RabbitMQ multi-node cluster failover and queue promotion.
- Kubernetes autoscaling behaviour under real load (the HPA is declared, not
  load-tested in a cluster).

## A practical target

For a production deployment that is genuinely highly available:

1. **Managed PostgreSQL** with a standby and automated failover, WAL archiving
   for PITR, and a scheduled `pg_dump`. This removes the critical SPOF.
2. **RabbitMQ as a 3-node cluster** with quorum queues, so the broker survives a
   node loss, plus a definitions backup.
3. **Redis replicated with failover**, understood to be non-critical.
4. **At least two replicas each** of `web`, `scheduler`, `publisher` and
   `worker`, behind a load balancer, spread across availability zones.

The two single points of failure that remain in a default single-node deployment
are the PostgreSQL primary and the RabbitMQ node; both are addressed above, and
both are the reason the backup/restore runbook exists.
