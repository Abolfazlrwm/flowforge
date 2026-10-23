"""Find failed task runs and requeue them.

    export FLOWFORGE_URL=http://localhost:8000
    export FLOWFORGE_TOKEN=...
    python examples/03_requeue_failed.py --dry-run
    python examples/03_requeue_failed.py --apply

This is the operator workflow for a run that ended ``failed`` but whose cause is
now fixed: a dependency was down, a credential expired, a bug shipped.

Only ``failed`` and ``dead`` tasks can be requeued — requeueing a running task
would execute it twice — so the script filters on the run's task states rather
than assuming every task is eligible.

``--dry-run`` is the default. Requeueing is a state change on live work, and a
script that does it because it was run without arguments is a footgun.
"""

from __future__ import annotations

import argparse
import os
import sys

from flowforge_client import ConflictError, FlowForge, Run, TaskRun

#: States a requeue can act on. `dead` means every attempt was used up, which is
#: exactly the case an operator requeues by hand.
REQUEUEABLE = frozenset({"failed", "dead"})


def main(argv: list[str] | None = None) -> int:
    """List (and optionally requeue) every eligible task run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually requeue. Without it the script only lists what it would do.",
    )
    args = parser.parse_args(argv)

    url = os.environ.get("FLOWFORGE_URL", "http://localhost:8000")
    token = os.environ.get("FLOWFORGE_TOKEN")
    if not token:
        print("set FLOWFORGE_TOKEN (see `manage.py e2e_token`)", file=sys.stderr)
        return 2

    with FlowForge(url, token=token) as client:
        candidates: list[tuple[Run, TaskRun]] = []
        # `list_runs` follows pagination, so this covers every run the credential
        # can see, not just the first page.
        for summary in client.list_runs():
            if summary.state not in ("failed", "running"):
                continue
            run = client.get_run(summary.id)
            candidates.extend((run, task) for task in run.task_runs if task.state in REQUEUEABLE)

        if not candidates:
            print("nothing to requeue")
            return 0

        for run, task in candidates:
            line = f"{run.id}  {run.workflow_name} v{run.workflow_version}  {task.task_key}"
            if not args.apply:
                print(f"would requeue  {line}")
                continue
            try:
                requeued = client.requeue_task_run(task.id)
            except ConflictError as exc:
                # A task that finished while this script was running is no longer
                # requeueable. That is a race, not a failure.
                print(f"skipped        {line} ({exc.code})")
                continue
            print(f"requeued       {line} -> {requeued.state}")

        if not args.apply:
            print(f"\n{len(candidates)} task run(s) eligible; rerun with --apply to requeue")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
