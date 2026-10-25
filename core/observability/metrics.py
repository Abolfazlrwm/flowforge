"""Prometheus metrics for every FlowForge process.

Two kinds of metric, deliberately:

* **Process-local** counters and histograms (task latency, retries, lease
  expirations). The worker that scrapes them reports its own work; summing them
  across workers is the aggregator's job, not the process's.
* **Durable-state gauges** (runs by state, outbox pending/lag, DEAD count).
  These are queried at scrape time, so they cannot drift from the database, and
  every process that exposes an endpoint reports the same global view.

The registry is module-level and shared by the web view and the scheduler,
publisher and worker HTTP servers, so one process has one coherent registry.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from django.db.models import Count
from django.utils import timezone
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from prometheus_client.core import GaugeMetricFamily

from core.domain.states import TaskRunState
from core.models import OutboxMessage, ScheduledTrigger, TaskRun, WorkflowRun

#: The one registry this process exposes. `auto_describe=False` keeps the
#: `_created` series and the per-collector metadata out of the output.
REGISTRY = CollectorRegistry(auto_describe=False)

#: Handler execution latency, observed by the worker around the handler call.
TASK_LATENCY = Histogram(
    "flowforge_task_latency_seconds",
    "Task handler execution latency",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

#: A retry was scheduled (a task moved FAILED -> RETRYING).
TASK_RETRIES = Counter(
    "flowforge_task_retries_total",
    "Task retries scheduled",
    registry=REGISTRY,
)

#: A lease expired and the reaper reclaimed the task.
LEASE_EXPIRATIONS = Counter(
    "flowforge_lease_expirations_total",
    "Leases reclaimed after expiry",
    registry=REGISTRY,
)

#: Messages waiting in the dispatch queue, as reported by the broker.
QUEUE_DEPTH = Gauge(
    "flowforge_queue_depth",
    "Messages in the dispatch queue",
    ["queue"],
    registry=REGISTRY,
)


class DatabaseCollector:
    """Collect the gauges that are derived from durable state.

    Queried at scrape time rather than updated on every write: a gauge that is
    recomputed on scrape cannot drift from the database, and every query here is
    an indexed count or a single-row lookup, not a scan.
    """

    def collect(self) -> Iterator[Any]:
        """Yield one metric family per durable-state gauge."""
        runs = GaugeMetricFamily(
            "flowforge_workflow_runs", "Workflow runs by state", labels=["state"]
        )
        for row in WorkflowRun.objects.values("state").annotate(count=Count("id")):
            runs.add_metric([row["state"]], row["count"])
        yield runs

        tasks = GaugeMetricFamily("flowforge_task_runs", "Task runs by state", labels=["state"])
        for row in TaskRun.objects.values("state").annotate(count=Count("id")):
            tasks.add_metric([row["state"]], row["count"])
        yield tasks

        pending = OutboxMessage.objects.filter(published_at__isnull=True)
        yield GaugeMetricFamily(
            "flowforge_outbox_pending",
            "Undelivered outbox rows",
            value=pending.count(),
        )
        oldest = pending.order_by("created_at").values_list("created_at", flat=True).first()
        lag = (timezone.now() - oldest).total_seconds() if oldest is not None else 0.0
        yield GaugeMetricFamily(
            "flowforge_outbox_lag_seconds",
            "Age in seconds of the oldest undelivered outbox row",
            value=lag,
        )
        yield GaugeMetricFamily(
            "flowforge_dead_tasks",
            "Task runs in the DEAD state",
            value=TaskRun.objects.filter(state=TaskRunState.DEAD.value).count(),
        )
        yield GaugeMetricFamily(
            "flowforge_scheduled_triggers_due",
            "Enabled scheduled triggers whose next run is due",
            value=ScheduledTrigger.objects.filter(
                enabled=True, next_run_at__lte=timezone.now()
            ).count(),
        )


REGISTRY.register(DatabaseCollector())


def metrics_port_argument(value: str) -> int:
    """An argparse ``type`` for ``--metrics-port``.

    An empty string means "disabled", not "invalid". Docker Compose interpolates
    an unset variable to an empty string, so a container that inherits
    ``--metrics-port ""`` from the compose file would otherwise die on
    ``int("")`` before the process ever starts. A genuinely malformed value
    still raises, which argparse turns into a usage error.
    """
    return int(value.strip() or 0)


def render_metrics() -> bytes:
    """Render the registry in the Prometheus text exposition format."""
    return generate_latest(REGISTRY)


def set_queue_depth(queue: str, depth: int) -> None:
    """Record the broker's message count for ``queue``."""
    QUEUE_DEPTH.labels(queue=queue).set(depth)


def start_metrics_server(
    port: int,
    # Inside a container the scrape happens over the compose network, not from
    # outside it; see the docstring. The annotation says only the test id: bandit
    # reads a trailing comment as more test names and warns about each word.
    addr: str = "0.0.0.0",  # noqa: S104  # nosec B104
) -> None:
    """Expose this process's registry over HTTP for Prometheus.

    Binding all interfaces is correct *inside* a container: the scrape happens
    over the compose network, not from outside it. The web process exposes the
    same registry at ``/api/v1/metrics/`` instead of opening a second port.
    """
    start_http_server(port, addr=addr, registry=REGISTRY)
