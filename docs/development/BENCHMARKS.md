# Benchmarks

Every number on this page was measured, and the raw output is committed beside
it. Nothing here is estimated.

**Read this first:** these are CI-runner numbers, not production numbers. The
load generator, the database, the broker and the workers all share one 4-vCPU
virtual machine, so the figures are a *floor* for what the same code does on
separated hardware. They are useful for comparing a change against its parent,
which is what they are used for below.

## Environment

Both load-test runs:

| | |
| --- | --- |
| Runner | GitHub Actions `ubuntu-latest` |
| CPU | 4 vCPU, AMD EPYC 9V74 |
| Memory | 15 GiB (1.4 GiB used) |
| OS | Linux 6.17.0-1022-azure x86_64 |
| Docker | 28.0.4 |
| Python | 3.13.16 |
| Locust | 2.47.0 |
| Database | PostgreSQL 16-alpine, containerised on the same host |
| Broker | RabbitMQ 3.13-alpine, containerised on the same host |
| Web server | Django `runserver` (development server, not gunicorn) |

The `runserver` detail matters: it is single-threaded per request and is not what
a deployment runs. The trigger latency below is therefore pessimistic.

Raw environment capture: `docs/development/benchmarks/scheduler-0.25s/environment.txt`.

## Load test

The command is the one in [Load Testing](load-testing.md):

```
uv run locust -f loadtests/locustfile.py --headless --only-summary \
  --users 10 --spawn-rate 2 --run-time 60s \
  --csv loadtest-results/run --host http://localhost:8000
```

The workload is a four-task diamond: `seed` → (`left`, `right`) → `combine`.
Each virtual user triggers a run and polls it to a terminal state.

Two configuration changes are made for the benchmark, and both are stated because
they change what is being measured:

| Setting | Shipped default | Benchmark | Why |
| --- | --- | --- | --- |
| `THROTTLE_TRIGGER_RATE` | `60/min` | `100000/min` | 10 users exhaust 60/min in the first second; the first attempt recorded 1469 `429`s and measured the throttle. |
| `SCHEDULER_INTERVAL` | `5` | `0.25` | See below — the poll cadence sets a floor on end-to-end latency. |

The shipped defaults are asserted by a unit test, so the knob cannot silently
loosen the policy.

## Result 1 — default scheduler interval (5 s)

10 users, 60 seconds, `SCHEDULER_INTERVAL=5`.

| Metric | Value |
| --- | --- |
| Triggers accepted | 31 |
| Runs completed | 31 (124 task runs, all four per run) |
| Failures | **0** |
| Throughput | 0.36 runs/s, 32.8 HTTP req/s |
| `POST /api/v1/runs/` median / p95 | 62 ms / 150 ms |
| `GET /api/v1/runs/<id>/` median / p95 | 44 ms / 130 ms |
| **Run end-to-end median** | **20 000 ms** |
| Run end-to-end p95 / max | 21 000 ms / 21 012 ms |

Raw: `docs/development/benchmarks/scheduler-5s/`.

**The 20-second figure is the scheduler's poll interval, not the engine.** A
four-task diamond needs four passes (seed, the two branches, the join, settle),
and four passes at 5 s is 20 s. The minimum of 15 750 ms is the same arithmetic
with a pass that happened to land early.

This is worth stating plainly because it is the single most important
operational fact about FlowForge's latency: **a run cannot finish faster than
`dependency_depth × scheduler_interval`.** The default trades latency for load,
which is the right trade for a batch workflow engine and the wrong one for
anything interactive.

## Result 2 — scheduler interval 0.25 s

Same shape, `SCHEDULER_INTERVAL=0.25`, so the number reflects the pipeline rather
than the polling schedule.

| Metric | Value | vs Result 1 |
| --- | --- | --- |
| Triggers accepted | 157 | 5.1× |
| Runs completed | 160 (640 task runs) | 5.2× |
| Failures | **0** | — |
| Throughput | 2.54 runs/s, 37.0 HTTP req/s | 7.0× runs/s |
| `POST /api/v1/runs/` median / p95 / p99 | 77 ms / 260 ms / 360 ms | p95 1.7× |
| `GET /api/v1/runs/<id>/` median / p95 | 51 ms / 170 ms | p95 1.3× |
| **Run end-to-end median** | **3 500 ms** | **5.7× faster** |
| Run end-to-end p95 / p99 / max | 4 100 / 4 300 / 4 424 ms | — |
| Task throughput | ~10.7 tasks/s | — |

Raw: `docs/development/benchmarks/scheduler-0.25s/`.

`RUNS=160` and `TASKS=640` come from a direct database count after the run, not
from Locust: they prove every accepted trigger produced a completed run, which
an HTTP-only report cannot show.

The trigger p95 rising from 150 ms to 260 ms while throughput rises 5× is the
expected shape — more concurrent work, more contention on one 4-vCPU box with a
single-threaded dev server.

## Result 3 — the profiled bottleneck

### What was wrong

`transition_task_run` records an event-log row through `_record_task_run_event`.
That helper accepts an optional `organization_id` and, when it is absent, fetches
it with a separate `SELECT`:

```python
if organization_id is None:
    organization_id = (
        TaskRun.objects.filter(pk=task_run_id).values_list("organization_id", flat=True).first()
    )
```

No caller ever passed it — even though every one of the five call sites is
iterating a queryset of `TaskRun` rows and therefore already holds the tenant.
The scheduler promotes every run on every pass, so on a 14-task diamond that is
13 wasted round trips per run.

### Measurement

`scripts/profile_scheduler.py --micro` runs both call shapes in one process,
against the same rows, so the only difference is the change:

```
uv run python scripts/profile_scheduler.py --runs 4 --fanout 6 \
  --micro --transitions 400
```

| | Queries per transition | Median time per transition |
| --- | --- | --- |
| Before (lookup inside the recorder) | 5.00 | ~3 100 µs |
| After (tenant passed in) | **4.00** | ~1 890 µs |
| Change | **−20%** | **≈ −39%** |

End to end, over 140 `advance_workflow_run` passes (10 runs × 14 tasks):

| | Total queries | Per pass | Wasted lookups |
| --- | --- | --- | --- |
| Before | 1 672 | 11.9 | 130 |
| After | **1 542** | **11.0** | **0** |

The query counts are exact and reproduce on every run. The wall-clock figures
are the median of three samples on a laptop running SQLite; the spread between
samples was ±15%, so treat the percentage as indicative and the query counts as
the fact.

### Why this bottleneck and not a bigger one

A full `cProfile` of `schedule_once` showed `load_definition` at ~19% of the
time — it re-queries the workflow's tasks and rebuilds the dependency graph on
every pass. Caching it would be faster, but it would weaken a deliberate
guarantee: `load_definition` re-validates the stored rows so a manual `UPDATE`
or a bad migration is caught at schedule time rather than surfacing later. The
query fix removes work without removing a check, so it is the one that was
taken. The cache is recorded as a limitation below rather than silently applied.

## Reproducing

```bash
# the load test
docker compose up -d --build
docker compose exec -T web python manage.py loadtest_user
THROTTLE_TRIGGER_RATE=100000/min THROTTLE_READ_RATE=100000/min \
  THROTTLE_AUTH_RATE=100000/min SCHEDULER_INTERVAL=0.25 \
  docker compose up -d --build
uv run locust -f loadtests/locustfile.py --headless --only-summary \
  --users 10 --spawn-rate 2 --run-time 60s \
  --csv loadtest-results/run --host http://localhost:8000

# the profiling comparison
uv run python manage.py migrate --noinput
uv run python scripts/profile_scheduler.py --runs 4 --fanout 6 --micro
```

Both are also what CI runs: the `loadtest` job produces the artifacts committed
above, and the `docker` job runs the same stack end to end.

## Limitations

- **CI hardware, shared host.** Load generator, database, broker and workers on
  one 4-vCPU VM. Absolute numbers are a floor.
- **Django `runserver`, not gunicorn.** Trigger latency is pessimistic; a
  production WSGI server would be substantially faster per request.
- **One worker process, `prefetch=1`.** Task throughput scales with worker
  replicas; the figures above are for a single worker container.
- **60 seconds, 10 users.** Long enough to be stable, short enough that a warm-up
  transient is a visible fraction of the window.
- **The result-1 environment was not captured to the artifact.** At the time, the
  job wrote it to `/tmp`; that was fixed before result 2. Result 1 and result 2
  ran on the same `ubuntu-latest` image, but the CPU/memory lines in this page
  come from result 2's capture.
- **`load_definition` is still re-queried per pass.** See above — a deliberate
  choice, not an oversight.
