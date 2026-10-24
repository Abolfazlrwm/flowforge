"""The full user journey through the ``flowforge`` command line.

The workflow is published through the SDK (the CLI has no publish command), then
the CLI drives it: trigger-and-wait, status, events, and health. Exit codes and
JSON output are asserted, so a regression in the CLI surface fails here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from ._support import SDK_DIR, base_url, sample_definition, token, unique_name

pytestmark = pytest.mark.e2e


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the ``flowforge`` CLI as a module against the live stack."""
    env = {
        **os.environ,
        "FLOWFORGE_URL": base_url(),
        "FLOWFORGE_TOKEN": token(),
        "PYTHONPATH": SDK_DIR + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    return subprocess.run(  # noqa: S603 - fixed, trusted command
        [sys.executable, "-m", "flowforge_client.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=200,
        check=False,
    )


def _extract_json(text: str) -> dict:
    """Parse the JSON object out of CLI output that may carry a leading line."""
    return json.loads(text[text.index("{") :])


def test_the_cli_drives_a_run_to_success(client) -> None:
    name = unique_name("e2e-cli")
    client.publish(sample_definition(name))

    triggered = _cli("--json", "trigger", name, "--version", "1", "--wait-timeout", "150")
    assert triggered.returncode == 0, triggered.stderr
    run = _extract_json(triggered.stdout)
    assert run["state"] == "succeeded", run
    run_id = run["id"]

    status = _cli("--json", "status", run_id)
    assert status.returncode == 0, status.stderr
    assert _extract_json(status.stdout)["state"] == "succeeded"

    events = _cli("events", run_id)
    assert events.returncode == 0, events.stderr
    assert "seed" in events.stdout, events.stdout

    health = _cli("--json", "health")
    assert health.returncode == 0, health.stderr
    assert _extract_json(health.stdout)["status"] == "ready"
