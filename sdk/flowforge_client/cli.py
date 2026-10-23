"""The ``flowforge`` command line.

A thin layer over :class:`flowforge_client.FlowForge`: every subcommand maps to
one client call, so the CLI cannot do anything the SDK cannot, and a bug fixed in
one is fixed in both.

Configuration comes from the environment first and flags second, because a CI
job sets the environment once and then runs several commands:

    FLOWFORGE_URL    base URL, e.g. http://localhost:8000
    FLOWFORGE_TOKEN  a DRF token or an ``ff_...`` API key

Exit codes are meaningful, so a shell can branch on them without parsing output:

    0  success
    1  the command failed (API error, transport error, timeout)
    2  usage error (argparse's own code)

``--json`` prints the raw payload instead of a table, for scripting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from .client import FlowForge, new_idempotency_key
from .errors import FlowForgeError
from .models import Run, Workflow

EXIT_OK = 0
EXIT_FAILED = 1

#: Where a run or task in a non-success terminal state lands. Used to pick the
#: exit code, not to decide whether to print.
FAILURE_STATES = frozenset({"failed", "dead", "cancelled"})


def _client(args: argparse.Namespace) -> FlowForge:
    """Build a client from the flags, falling back to the environment.

    Raises:
        SystemExit: with code 2 (the usage-error code) when there is no URL. A
            CLI that guesses a host is worse than one that refuses.

    """
    url = args.url or os.environ.get("FLOWFORGE_URL")
    if not url:
        print(
            "error: no API URL: pass --url or set FLOWFORGE_URL (e.g. http://localhost:8000)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    token = args.token or os.environ.get("FLOWFORGE_TOKEN")
    return FlowForge(url, token=token, timeout=args.timeout)


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """Print a left-aligned table with a header rule."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))


def _emit(
    args: argparse.Namespace, payload: Any, rows: Sequence[Sequence[Any]], headers: Sequence[str]
) -> None:
    """Print either the raw JSON or a table, depending on ``--json``."""
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_table(headers, rows)


# -- commands ---------------------------------------------------------------


def cmd_health(args: argparse.Namespace) -> int:
    """Print the readiness payload."""
    payload = _client(args).health()
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        for dependency, state in payload.items():
            print(f"{dependency}: {state}")
    healthy = payload.get("status") == "ready"
    return EXIT_OK if healthy else EXIT_FAILED


def cmd_workflows(args: argparse.Namespace) -> int:
    """List published workflows."""
    summaries = list(_client(args).list_workflows())
    _emit(
        args,
        [_as_dict(summary) for summary in summaries],
        [(s.name, s.version, s.task_count, s.created_at or "-") for s in summaries],
        ("NAME", "VERSION", "TASKS", "CREATED"),
    )
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    """Show one workflow definition, task by task."""
    workflow: Workflow = _client(args).get_workflow(args.name, args.version)
    if args.json:
        print(json.dumps(_as_dict(workflow), indent=2, default=str))
        return EXIT_OK
    print(f"{workflow.name} v{workflow.version}")
    if workflow.description:
        print(f"  {workflow.description}")
    _print_table(
        ("KEY", "TYPE", "DEPENDS ON"),
        [(task.key, task.task_type, ", ".join(task.depends_on) or "-") for task in workflow.tasks],
    )
    return EXIT_OK


def cmd_trigger(args: argparse.Namespace) -> int:
    """Trigger a run and, unless --no-wait, follow it to a terminal state."""
    client = _client(args)
    key = args.idempotency_key or new_idempotency_key()
    run = client.trigger(args.name, version=args.version, idempotency_key=key)

    if args.json and args.no_wait:
        print(json.dumps(_as_dict(run), indent=2, default=str))
        return EXIT_OK

    print(f"triggered run {run.id} ({run.workflow_name} v{run.workflow_version})")
    if args.no_wait:
        return EXIT_OK

    try:
        run = client.wait(run.id, timeout=args.wait_timeout)
    except TimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    _print_run(run, as_json=args.json)
    return EXIT_OK if run.succeeded else EXIT_FAILED


def cmd_runs(args: argparse.Namespace) -> int:
    """List runs."""
    summaries = list(_client(args).list_runs())
    _emit(
        args,
        [_as_dict(summary) for summary in summaries],
        [(s.id, s.workflow_name, s.workflow_version, s.state) for s in summaries],
        ("ID", "WORKFLOW", "VERSION", "STATE"),
    )
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    """Show one run, task by task."""
    run = _client(args).get_run(args.run_id)
    _print_run(run, as_json=args.json)
    return EXIT_OK if run.state not in FAILURE_STATES else EXIT_FAILED


def cmd_events(args: argparse.Namespace) -> int:
    """Show a run's event log."""
    events = _client(args).run_events(args.run_id)
    _emit(
        args,
        [_as_dict(event) for event in events],
        [
            (e.created_at or "-", e.task_key, e.kind, f"{e.from_state} -> {e.to_state}")
            for e in events
        ],
        ("WHEN", "TASK", "KIND", "TRANSITION"),
    )
    return EXIT_OK


def cmd_requeue(args: argparse.Namespace) -> int:
    """Requeue a failed or dead task run."""
    task_run = _client(args).requeue_task_run(args.task_run_id)
    print(f"task {task_run.task_key} is now {task_run.state}")
    return EXIT_OK


def _print_run(run: Run, *, as_json: bool) -> None:
    """Print a run and its tasks."""
    if as_json:
        print(json.dumps(_as_dict(run), indent=2, default=str))
        return
    print(f"run {run.id}  {run.workflow_name} v{run.workflow_version}  [{run.state}]")
    _print_table(
        ("TASK", "STATE", "ATTEMPT", "STARTED", "FINISHED"),
        [
            (
                task.task_key,
                task.state,
                task.attempt,
                task.started_at or "-",
                task.finished_at or "-",
            )
            for task in run.task_runs
        ],
    )


def _as_dict(obj: Any) -> Any:
    """Convert a frozen, slotted dataclass into a dict.

    ``asdict`` is the only reliable way to serialise one: a slots dataclass has no
    ``__dict__``, so ``vars(obj)`` raises.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return obj


def build_parser() -> argparse.ArgumentParser:
    """Build the full command tree.

    Built as a function rather than at import time so tests can construct a
    parser without touching ``sys.argv``.
    """
    parser = argparse.ArgumentParser(
        prog="flowforge",
        description="Talk to a FlowForge deployment.",
    )
    parser.add_argument("--url", help="API base URL (or FLOWFORGE_URL).")
    parser.add_argument("--token", help="DRF token or ff_ API key (or FLOWFORGE_TOKEN).")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a table.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Show dependency readiness.")
    health.set_defaults(func=cmd_health)

    workflows = subparsers.add_parser("workflows", help="List published workflows.")
    workflows.set_defaults(func=cmd_workflows)

    show = subparsers.add_parser("show", help="Show one workflow definition.")
    show.add_argument("name")
    show.add_argument("--version", type=int, default=1)
    show.set_defaults(func=cmd_show)

    trigger = subparsers.add_parser("trigger", help="Trigger a run and wait for it.")
    trigger.add_argument("name")
    trigger.add_argument("--version", type=int, default=1)
    trigger.add_argument(
        "--idempotency-key",
        help="Reuse a key to replay instead of starting a second run.",
    )
    trigger.add_argument("--no-wait", action="store_true", help="Return as soon as it is accepted.")
    trigger.add_argument(
        "--wait-timeout", type=float, default=300.0, help="Seconds to wait (default: 300)."
    )
    trigger.set_defaults(func=cmd_trigger)

    runs = subparsers.add_parser("runs", help="List runs.")
    runs.set_defaults(func=cmd_runs)

    status = subparsers.add_parser("status", help="Show one run.")
    status.add_argument("run_id")
    status.set_defaults(func=cmd_status)

    events = subparsers.add_parser("events", help="Show a run's event log.")
    events.add_argument("run_id")
    events.set_defaults(func=cmd_events)

    requeue = subparsers.add_parser("requeue", help="Requeue a failed or dead task run.")
    requeue.add_argument("task_run_id")
    requeue.set_defaults(func=cmd_requeue)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.func
    try:
        return handler(args)
    except FlowForgeError as exc:
        # The message already names the status and the code, which is what a
        # shell script needs to decide what to do next.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
