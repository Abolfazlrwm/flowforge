r"""Measure the scheduler's cost per run, and attribute it.

This is the tool behind the before/after numbers in
``docs/development/BENCHMARKS.md``. It needs no external services: it runs
against the SQLite test database, so a number measured here is a *relative*
measurement, not a throughput figure. Use it to compare a change against its
parent, not to size a deployment.

    uv run python manage.py migrate --noinput   # once
    uv run python scripts/profile_scheduler.py --micro

The workload is a wide diamond -- one seed, a fan-out, one join -- because that
is the shape the scheduler's promotion loop actually has to think about, and the
shape a real ETL workflow has. The fan-out width and the run count are flags, so
a measurement is fully described by the command that produced it.

``--micro`` is the low-noise measurement. The end-to-end pass timing is
dominated by SQLite filesystem noise on a laptop, which makes a before/after
comparison meaningless; the micro-benchmark times a tight loop of
``transition_task_run`` calls and reports both the old and the new call shape in
the same process, so the comparison is not confounded by machine state.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import os
import pstats
import sys
import time
from collections import Counter
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowforge.settings.test")
django.setup()

from core.domain.definition import TaskDefinition, WorkflowDefinition  # noqa: E402
from core.domain.states import TaskRunState  # noqa: E402
from core.models import (  # noqa: E402
    AuditLog,
    IdempotencyKey,
    Organization,
    OutboxMessage,
    ScheduledTrigger,
    TaskRun,
    TaskRunEvent,
    Workflow,
    WorkflowRun,
)
from core.repositories.definitions import (  # noqa: E402
    DefinitionConflictError,
    publish_definition,
)
from core.repositories.scheduler import advance_workflow_run, schedule_once  # noqa: E402
from core.repositories.transitions import transition_task_run  # noqa: E402
from core.services.runs import trigger  # noqa: E402
from core.services.tenancy import ensure_default_organization  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402

WORKFLOW_NAME = "profile-wide"


def build_definition(fanout: int) -> WorkflowDefinition:
    """A diamond with ``fanout`` parallel branches between the seed and the join."""
    tasks = [TaskDefinition(key="seed", task_type="noop")]
    branches = [f"branch-{index:02d}" for index in range(fanout)]
    tasks.extend(
        TaskDefinition(key=key, task_type="noop", depends_on=("seed",)) for key in branches
    )
    tasks.append(TaskDefinition(key="join", task_type="noop", depends_on=tuple(branches)))
    return WorkflowDefinition(
        name=WORKFLOW_NAME, version=1, description="Profiling workload.", tasks=tuple(tasks)
    )


def _reset_state() -> None:
    """Clear the rows a previous measurement left behind, in FK order."""
    OutboxMessage.objects.all().delete()
    TaskRunEvent.objects.all().delete()
    TaskRun.objects.all().delete()
    ScheduledTrigger.objects.all().delete()
    WorkflowRun.objects.all().delete()
    AuditLog.objects.all().delete()
    IdempotencyKey.objects.all().delete()


def _query_shapes(queries: list[dict[str, str]]) -> Counter[str]:
    """Group captured queries by statement shape, so identical SQL groups."""
    shapes: Counter[str] = Counter()
    for query in queries:
        sql = " ".join(str(query["sql"]).split())
        if " FROM " in sql:
            head, _, tail = sql.partition(" FROM ")
            table = tail.split()[0]
        else:
            head, table = sql[:70], "?"
        shapes[f"{head.strip()[:60]} | {table}"] += 1
    return shapes


def _time_transitions(
    *, transitions: int, task_runs: list[TaskRun], pass_tenant: bool
) -> tuple[float, int]:
    """Time ``transitions`` calls to ``transition_task_run``; return (seconds, queries).

    ``pass_tenant=False`` reproduces the pre-optimisation call shape, where the
    tenant was looked up inside the event recorder instead of being handed in.
    Both variants run against the same rows in the same process, so the only
    difference between them is the change under test.

    Each iteration resets one row to PENDING and then performs a real
    PENDING -> READY transition, so every measured call does the full amount of
    work (a conditional UPDATE plus an event-log INSERT). The reset runs with the
    debug cursor off, so it is not counted.
    """
    tenant = task_runs[0].organization_id
    # One outer transaction, so the inner `atomic()` blocks become savepoints
    # instead of commits. Without it every iteration pays a SQLite fsync, and the
    # measurement becomes a disk benchmark with a ±40% spread that buries the
    # effect being measured.
    with CaptureQueriesContext(connection) as ctx, transaction.atomic():
        started = time.perf_counter()
        for index in range(transitions):
            task_run = task_runs[index % len(task_runs)]

            connection.force_debug_cursor = False
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        'UPDATE "core_taskrun" SET "state" = %s WHERE "id" = %s',
                        [TaskRunState.PENDING.value, task_run.id.hex],
                    )
            finally:
                connection.force_debug_cursor = True

            with transaction.atomic():
                if pass_tenant:
                    transition_task_run(
                        task_run_id=task_run.id,
                        expected=TaskRunState.PENDING,
                        target=TaskRunState.READY,
                        organization_id=tenant,
                    )
                else:
                    transition_task_run(
                        task_run_id=task_run.id,
                        expected=TaskRunState.PENDING,
                        target=TaskRunState.READY,
                    )
        elapsed = time.perf_counter() - started
    return elapsed, len(ctx.captured_queries)


def _micro_benchmark(*, transitions: int, task_runs: list[TaskRun]) -> None:
    """Compare the old and new transition call shapes in one process."""
    # Warm up both paths so the first-call cost does not land on whichever runs
    # first, and so the comparison is between steady states.
    _time_transitions(transitions=20, task_runs=task_runs, pass_tenant=True)
    _time_transitions(transitions=20, task_runs=task_runs, pass_tenant=False)

    for label, pass_tenant in (("without tenant", False), ("with tenant", True)):
        samples = [
            _time_transitions(transitions=transitions, task_runs=task_runs, pass_tenant=pass_tenant)
            for _ in range(3)
        ]
        best = min(samples, key=lambda sample: sample[0])
        median = sorted(sample[0] for sample in samples)[1]
        queries = samples[0][1]
        print(
            f"micro [{label:14s}]: {median:.3f}s median / {best[0]:.3f}s best "
            f"for {transitions} transitions "
            f"({median / transitions * 1e6:.1f} us each), "
            f"{queries} queries ({queries / transitions:.2f} per transition)"
        )


def main(argv: list[str] | None = None) -> int:
    """Run the measurement and print the timings, query counts and profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fanout", type=int, default=12, help="Parallel branches (default: 12).")
    parser.add_argument("--runs", type=int, default=25, help="Runs to advance (default: 25).")
    parser.add_argument("--profile", action="store_true", help="Also print a cProfile report.")
    parser.add_argument("--micro", action="store_true", help="Also time transition_task_run.")
    parser.add_argument(
        "--transitions", type=int, default=400, help="Transitions for --micro (default: 400)."
    )
    args = parser.parse_args(argv)

    organization: Organization = ensure_default_organization()
    _reset_state()

    definition = build_definition(args.fanout)
    try:
        workflow, _ = publish_definition(definition, organization=organization)
    except DefinitionConflictError:
        workflow = Workflow.objects.get(name=WORKFLOW_NAME, version=1, organization=organization)

    task_count = len(definition.tasks)
    runs = [
        trigger(workflow, requested_by_id=None, organization=organization)[0]
        for _ in range(args.runs)
    ]
    print(f"workflow: {task_count} tasks ({args.fanout} branches); runs: {len(runs)}")

    # Warm up: first-graph construction, connection setup, import of the graph
    # module. Without this the "before" number includes one-off costs that the
    # "after" run would not pay again.
    for run in runs[:3]:
        advance_workflow_run(run)

    # Advance every run to a fixed point, the way the scheduler does over several
    # passes, with a worker completing the dispatched tasks in between.
    #
    # Without the completion step a single pass promotes at most one task per
    # branch -- a dependant is only eligible once its dependency has SUCCEEDED,
    # not merely been dispatched -- so the measurement would exercise almost none
    # of the promotion loop. The completion UPDATE runs with the debug cursor off
    # so it does not count towards the scheduler's query budget.
    with CaptureQueriesContext(connection) as ctx:
        started = time.perf_counter()
        passes = 0
        for run in runs:
            for _ in range(args.fanout + 2):
                advance_workflow_run(run)
                passes += 1
                connection.force_debug_cursor = False
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'UPDATE "core_taskrun" SET "state" = %s '
                            'WHERE "workflow_run_id" = %s AND "state" = %s',
                            [
                                TaskRunState.SUCCEEDED.value,
                                # `.hex`, not `str(...)`: the column stores the
                                # dash-less form and `str()` inserts dashes, which
                                # silently matches nothing.
                                run.id.hex,
                                TaskRunState.READY.value,
                            ],
                        )
                finally:
                    connection.force_debug_cursor = True
        elapsed = time.perf_counter() - started

    print(
        f"advance_workflow_run: {elapsed:.3f}s total over {passes} passes, "
        f"{elapsed / passes * 1000:.2f} ms/pass"
    )
    print(
        f"  queries: {len(ctx.captured_queries)} total, "
        f"{len(ctx.captured_queries) / passes:.1f} per pass"
    )
    for shape, count in _query_shapes(list(ctx.captured_queries)).most_common(10):
        print(f"  {count:5d}  {shape}")

    started = time.perf_counter()
    reaped, enqueued = schedule_once()
    print(
        f"schedule_once: {time.perf_counter() - started:.3f}s "
        f"(reaped={reaped}, enqueued={enqueued})"
    )

    if args.micro:
        task_runs = list(TaskRun.objects.filter(workflow_run=runs[0]))
        _micro_benchmark(transitions=args.transitions, task_runs=task_runs)

    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(3):
            schedule_once()
        profiler.disable()
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(25)
        print(stream.getvalue())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
