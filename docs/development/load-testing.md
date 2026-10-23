# Load Testing

## The command

```bash
# 1. Start the stack
docker compose up -d --build

# 2. Create the load-test user (idempotent)
docker compose exec -T web python manage.py loadtest_user

# 3. Run the test
docker compose exec -T web python manage.py loadtest_user >/dev/null   # once per stack
uv run locust \
  -f loadtests/locustfile.py \
  --headless \
  --only-summary \
  --users 10 \
  --spawn-rate 2 \
  --run-time 60s \
  --csv loadtest-results/run \
  --host http://localhost:8000
```

Step 3 runs from the host, not inside a container, so the CSV lands in
`./loadtest-results/`. Locust's exit code is non-zero if any request failed,
which is deliberate: a failure under load is a defect, not a result to archive.

## What the test measures

`loadtests/locustfile.py` drives the whole pipeline, not just the HTTP surface.
Each virtual user authenticates once, publishes a four-task diamond workflow,
then repeatedly triggers a run and polls it to a terminal state.

Three numbers are reported, and they answer different questions:

| Name | What it is |
| --- | --- |
| `POST /api/v1/runs/` | The HTTP round trip that creates the run. |
| `trigger` | The same round trip, recorded as a custom event so it can be read next to the pipeline latency. |
| `run end-to-end` | Trigger accepted to the run reaching `succeeded`, which includes the scheduler, the outbox, the publisher, RabbitMQ and the worker. |

The end-to-end number is the one that matters for a workflow engine, and it is
also the one a pure HTTP load test cannot produce.

A unique `Idempotency-Key` is sent with every trigger, so a Locust-level retry
replays the original run instead of creating a second one — the same contract a
real client relies on.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOCUST_HOST` | `http://localhost:8000` | Base URL of the API. |
| `LOCUST_USERNAME` | `loadtest` | API username. |
| `LOCUST_PASSWORD` | `loadtest-password` | API password. |

Users, spawn rate and duration are command-line flags, so a run is fully
described by the command that produced it. **Change them and the numbers are not
comparable** — always record the command next to the result.

## Rate limits

The API throttles per caller: 10 logins/min, 60 triggers/min and 300 reads/min
by default. Ten virtual users triggering and polling exhaust that immediately,
and the first run of the CI job recorded **1469 `429` responses** and almost no
real measurements — it benchmarked the throttle.

The rates are therefore configurable, which a real deployment needs anyway:

```bash
THROTTLE_TRIGGER_RATE=100000/min \
THROTTLE_READ_RATE=100000/min \
THROTTLE_AUTH_RATE=100000/min \
  docker compose up -d --build
```

The CI job sets all three. **Any result produced with raised limits measures
capacity, not the shipped policy** — the report says so next to the numbers, and
a separate check asserts the shipped defaults are still what the docs claim.

## Reading the results

`--csv loadtest-results/run` writes one file per request name plus
`_stats.csv` and `_failures.csv`:

```
loadtest-results/
  run_auth_stats.csv
  run_trigger_stats.csv
  run_run_end-to-end_stats.csv
  run_stats.csv            # aggregated
  run_failures.csv
  run_exceptions.csv
```

The report in `docs/development/BENCHMARKS.md` is generated from these files
and states the machine and the configuration beside every number.

## What this test does not tell you

- **It is not a production benchmark.** The load generator shares a host with
  the database and the broker, so the absolute figures are a floor.
- **It does not test the scheduler's cron path.** `cron_e2e` covers that.
- **It does not test failure recovery under load.** The compose CI job's broker
  and PostgreSQL restarts cover that, on an idle stack.
- **It does not measure the worker's throughput directly.** `run end-to-end`
  is dominated by poll granularity (250 ms) at low load, which is why the
  report also records the durable outcome: how many runs and task runs actually
  completed.
