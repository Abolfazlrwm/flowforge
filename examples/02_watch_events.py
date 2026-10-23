"""Follow a run's event log and print every state change as it happens.

    export FLOWFORGE_URL=http://localhost:8000
    export FLOWFORGE_TOKEN=...
    python examples/02_watch_events.py <run-id>

A run's task runs change state through a small machine (pending -> ready ->
running -> succeeded, with retries and skips as detours). The event log records
each transition, so watching it is the clearest way to see what the engine is
actually doing — and the fastest way to tell "slow" from "stuck in ready".

The script prints new events as they appear and exits when the run settles.
"""

from __future__ import annotations

import os
import sys
import time

from flowforge_client import FlowForge

POLL_SECONDS = 1.0


def main(argv: list[str]) -> int:
    """Poll the event log until the run is terminal, printing new entries."""
    if len(argv) != 2:
        print("usage: 02_watch_events.py <run-id>", file=sys.stderr)
        return 2
    run_id = argv[1]

    url = os.environ.get("FLOWFORGE_URL", "http://localhost:8000")
    token = os.environ.get("FLOWFORGE_TOKEN")
    if not token:
        print("set FLOWFORGE_TOKEN (see `manage.py e2e_token`)", file=sys.stderr)
        return 2

    with FlowForge(url, token=token) as client:
        seen: set[str] = set()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            # The event log is append-only, so a set of ids is enough to print only
            # what is new. Re-reading the whole log is cheap: it is bounded by the
            # task count times the transitions each task can make.
            for event in client.run_events(run_id):
                if event.id in seen:
                    continue
                seen.add(event.id)
                transition = f"{event.from_state or '-'} -> {event.to_state or '-'}"
                print(f"{event.task_key:<12} {event.kind:<10} {transition}")

            run = client.get_run(run_id)
            if run.is_terminal:
                print(f"\nrun ended {run.state}")
                return 0 if run.succeeded else 1
            time.sleep(POLL_SECONDS)

        print("gave up waiting after 300s", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
