"""Publish a workflow, run it, and report what happened.

This is the smallest complete program: one definition, one run, one answer.

    export FLOWFORGE_URL=http://localhost:8000
    export FLOWFORGE_TOKEN=...          # from `manage.py e2e_token` or an API key
    python examples/01_publish_and_run.py

The definition is a three-task pipeline: ``extract`` fans out to ``transform``,
and both feed ``load``. ``depends_on`` is what makes the scheduler wait.
"""

from __future__ import annotations

import os
import sys

from flowforge_client import FlowForge, new_idempotency_key

DEFINITION = {
    "name": "example-pipeline",
    "version": 1,
    "description": "Extract, transform, load — the smallest useful pipeline.",
    "tasks": [
        {"key": "extract", "task_type": "echo", "config": {"value": "raw"}},
        {
            "key": "transform",
            "task_type": "echo",
            "depends_on": ["extract"],
            "config": {"value": "clean"},
        },
        {"key": "load", "task_type": "noop", "depends_on": ["transform"]},
    ],
}


def main() -> int:
    """Publish, trigger, wait, and print each task's final state."""
    url = os.environ.get("FLOWFORGE_URL", "http://localhost:8000")
    token = os.environ.get("FLOWFORGE_TOKEN")
    if not token:
        print("set FLOWFORGE_TOKEN (see `manage.py e2e_token`)", file=sys.stderr)
        return 2

    with FlowForge(url, token=token) as client:
        workflow = client.publish(DEFINITION)
        print(f"published {workflow.name} v{workflow.version} ({len(workflow.tasks)} tasks)")

        # The key is what makes a retry safe. Without it, a timeout followed by a
        # retry would start a second run.
        run = client.trigger(
            workflow.name, version=workflow.version, idempotency_key=new_idempotency_key()
        )
        print(f"triggered run {run.id}")

        finished = client.wait(run.id, timeout=120)
        print(f"run ended {finished.state}")
        for task in finished.task_runs:
            print(f"  {task.task_key:<12} {task.state:<10} attempt {task.attempt}")

        # A run that ended failed still returns normally from `wait`: the state is
        # the answer, and raising would hide which task failed.
        return 0 if finished.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
