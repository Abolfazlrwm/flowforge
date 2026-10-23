# Observability

FlowForge is a distributed system: a request lands on the web process, the
scheduler turns it into an outbox row, the publisher hands that row to
RabbitMQ, and a worker in another process executes the task. A failure in the
middle of that chain is invisible unless the three signals — logs, metrics and
traces — are correlated by something other than a timestamp. This page is the
contract for all three.

## Correlation IDs

One id follows a run from the HTTP request through the outbox, the broker and
the worker.

| Where | What happens |
| --- | --- |
| API | `django-structlog` binds `request_id` (from `X-Request-ID`, or a generated UUID). `core.observability.context.current_correlation_id` reuses it, so a client that already sends a request id gets it echoed in every downstream log line. |
| `WorkflowRun` | The id is stored in `correlation_id` on the run row. |
| `OutboxMessage` | The id is stored on the row and repeated inside the dispatch payload. |
| Worker | `Worker.process` clears the context, then binds the message's `correlation_id`, `organization_id`, `dispatch_id`, `task_run_id` and `workflow_run_id`. |

Clearing before binding matters: a worker is long-lived, and without it the
previous message's run id would leak into the next line.

## Structured logs

Every process logs JSON. The configuration lives in
`flowforge/settings/base.py` — `structlog` with `JSONRenderer` for the app
loggers and a `ProcessorFormatter` for Django's own, so a single log line
carries `timestamp`, `level`, `logger`, `event` and every bound context field.

```json
{"timestamp": "2026-10-23T10:11:22.391Z", "level": "info", "logger": "flowforge.worker",
 "event": "task finished", "correlation_id": "9f1c...", "task_run_id": "...",
 "task_key": "extract", "state": "succeeded", "attempt": 1}
```

Read them with `docker compose logs --no-color web scheduler publisher worker`.
There is no tenant identifier, username or task config in any log line.

## Metrics

Every process owns one Prometheus registry. Two kinds of metric, deliberately:

- **Process-local** counters and histograms — `flowforge_task_latency_seconds`,
  `flowforge_task_retries_total`, `flowforge_lease_expirations_total`. The
  process that scrapes them reports only its own work; summing across workers
  is the aggregator's job.
- **Durable-state gauges** — `flowforge_workflow_runs`, `flowforge_task_runs`,
  `flowforge_outbox_pending`, `flowforge_outbox_lag_seconds`,
  `flowforge_dead_tasks`, `flowforge_scheduled_triggers_due`. These are queried
  at scrape time, so they cannot drift from the database, and every process
  reports the same global view.

| Process | Endpoint |
| --- | --- |
| web | `GET /api/v1/metrics/` on port 8000 |
| scheduler | `:9101/metrics` |
| publisher | `:9102/metrics` |
| worker | `:9103/metrics` |

The metrics endpoints on the scheduler, publisher and worker are opt-in:
`--metrics-port 0` (the default) disables them. The compose stack sets them.

`/api/v1/metrics/` is unauthenticated on purpose — a Prometheus sidecar should
not need a credential. The body is aggregate only: state counts, outbox depth
and lag, DEAD count and due triggers, with no tenant identifier, username or
task key. Bind it to a private network.

```bash
curl -s http://localhost:8000/api/v1/metrics/ | grep flowforge_dead_tasks
```

## Tracing

`core.observability.tracing` carries one trace from the API to the worker:

```
run.trigger            (web, root span)
└── outbox.dispatch    (scheduler)
    └── task.execute   (worker)
```

The outbox is the hard part. It crosses a process boundary and a time gap, so
there is no live span to inherit. The API's root `traceparent` is therefore
persisted on the run, copied onto the outbox row, carried in the dispatch
payload, and the worker continues it as a child. That is why `WorkflowRun` and
`OutboxMessage` both have a `traceparent` column.

The provider is module-local, not the OpenTelemetry global one: the global
provider can only be set once per process, which would make a test that
installs an in-memory exporter order-dependent. No auto-instrumentation is
used, so nothing depends on the global provider.

To export traces, set `OTEL_EXPORTER_OTLP_ENDPOINT` before starting a process.
Without it, spans are still created and are still linked to their logs by the
correlation id — they are simply not shipped anywhere.

## The observability profile

```bash
docker compose --profile observability up
```

That adds three services next to the unchanged application stack:

| Service | Image | Port |
| --- | --- | --- |
| `prometheus` | `prom/prometheus:v2.54.1` | 9090 |
| `grafana` | `grafana/grafana:11.3.1` | 3000 |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.114.0` | 4318, 4317 |

Everything under `deploy/observability/` is code: the scrape config, the
collector pipeline, the Grafana datasource and the dashboard. The dashboard
(`deploy/observability/grafana/dashboards/flowforge.json`) is provisioned
read-only, so a change to it is a reviewed diff rather than a click in a UI.

Metrics and dashboards work with no extra configuration. To also export traces:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
  docker compose --profile observability up
```

The endpoint is not set by default because a stack that is always trying to
export to a collector that is not running logs a failed export every batch
interval, and that noise would be worse than the missing traces.

## What to look at when something is wrong

| Symptom | First query |
| --- | --- |
| A run is stuck | `flowforge_task_runs{state="ready"}` — work the scheduler never dispatched, or `state="running"` with a rising `flowforge_lease_expirations_total` — workers dying mid-task. |
| Work is slow but nothing failed | `flowforge_outbox_lag_seconds` rising while `flowforge_outbox_pending` is flat is a publisher problem; both rising together is a throughput problem. |
| Tasks fail then recover | `flowforge_task_retries_total` rate against `flowforge_task_latency_seconds` p99 — a slow dependency times out. |
| A task is permanently broken | `flowforge_dead_tasks` — every attempt is exhausted and a human decision is needed. |
| Cron did not fire | `flowforge_scheduled_triggers_due` above zero for longer than one schedule interval. |

## Limitations

- **No alerting rules.** The profile ships a dashboard, not an Alertmanager
  configuration. Alert thresholds depend on what a deployment considers
  normal, and that is an operator decision.
- **Traces need one environment variable.** See above; the compose file
  deliberately does not set it.
- **The collector exports to its debug sink.** Point the `otlp` exporter in
  `deploy/observability/otel-collector-config.yaml` at a real backend (Tempo,
  Jaeger, a vendor) to keep spans.
- **RabbitMQ's own metrics come from its plugin.** The scrape config includes
  `rabbitmq:15692`, which the image enables by default. If a deployment
  disables it, that one target goes DOWN and the rest keep working.
- **Histogram buckets are fixed at code level.** `TASK_LATENCY` uses the
  bucket boundaries in `core/observability/metrics.py`; a quantile between two
  buckets is interpolated by Prometheus, not measured.
