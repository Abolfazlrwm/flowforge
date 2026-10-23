"""Locust load test for the FlowForge REST API and its execution pipeline.

The scenario is deliberately end-to-end rather than a pure HTTP hammer: a
trigger that is never waited on measures the API and nothing else, and the
scheduler/publisher/worker pipeline is where a workflow engine actually spends
its time. Each virtual user therefore

1. authenticates once (token auth, the same credential the compose e2e uses),
2. publishes a small diamond-shaped workflow once per user,
3. triggers runs and polls each one to a terminal state.

Two latencies are recorded as separate custom events, because they are
different numbers and averaging them together hides both:

* ``trigger`` — the HTTP round trip that creates the run, and
* ``run end-to-end`` — trigger accepted to the run reaching a terminal state,
  which is the number that includes the scheduler, the outbox, the publisher,
  the broker and the worker.

Run it with the documented command in ``docs/development/load-testing.md``.

Environment variables (all optional; the defaults target the compose stack):

    LOCUST_HOST      Base URL, default ``http://localhost:8000``
    LOCUST_USERNAME  API username, default ``loadtest``
    LOCUST_PASSWORD  API password, default ``loadtest-password``
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from locust import HttpUser, between, events, task

#: A diamond: ``seed`` fans out to two independent tasks, both feed ``combine``.
#: Four tasks per run is enough to exercise the fan-out/fan-in path without the
#: run taking so long that the poll loop dominates the measurement.
WORKFLOW_NAME = "loadtest-diamond"

WORKFLOW_DEFINITION: dict[str, Any] = {
    "name": WORKFLOW_NAME,
    "version": 1,
    "description": "The load-test workflow: a seed, two parallel branches, a join.",
    "tasks": [
        {"key": "seed", "task_type": "noop"},
        {
            "key": "left",
            "task_type": "echo",
            "depends_on": ["seed"],
            "config": {"value": "left"},
        },
        {
            "key": "right",
            "task_type": "add",
            "depends_on": ["seed"],
            "config": {"values": [1, 2, 3]},
        },
        {"key": "combine", "task_type": "noop", "depends_on": ["left", "right"]},
    ],
}

#: States that end a run. Anything else is still in flight.
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})

#: How long to poll a run before giving up. A run that has not settled in two
#: minutes is a stuck pipeline, not a slow one; recording it as a failure is
#: more useful than extending the timeout.
POLL_TIMEOUT_SECONDS = 120.0

#: Poll interval. Short enough that the measured end-to-end latency is dominated
#: by the pipeline rather than by the sampling interval, long enough that the
#: poll does not itself become the load.
POLL_INTERVAL_SECONDS = 0.25


def _record(name: str, elapsed_seconds: float, error: Exception | None = None) -> None:
    """Fire a Locust request event for a custom measurement.

    Locust only reports the requests its own HTTP client makes, so a latency
    that spans several of them (trigger -> settled) has to be recorded
    explicitly or it is simply missing from the report.
    """
    events.request.fire(
        request_type="CUSTOM",
        name=name,
        response_time=elapsed_seconds * 1000,
        response_length=0,
        exception=error,
        context={},
    )


class FlowForgeUser(HttpUser):
    """A client that triggers workflows and waits for them to finish."""

    # A short, jittered think time. Without it the test measures how fast Locust
    # can open sockets; with it, it measures a plausible request rate.
    wait_time = between(0.1, 0.5)
    host = os.environ.get("LOCUST_HOST", "http://localhost:8000")

    def on_start(self) -> None:
        """Authenticate once and publish the workflow this user will trigger."""
        self.username = os.environ.get("LOCUST_USERNAME", "loadtest")
        self.password = os.environ.get("LOCUST_PASSWORD", "loadtest-password")
        self.token: str | None = None
        self.workflow_ready = False

        with self.client.post(
            "/api/v1/auth/token/",
            json={"username": self.username, "password": self.password},
            name="auth",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                self.token = response.json().get("token")
            else:
                response.failure(f"auth returned {response.status_code}")
                return

        if not self.token:
            return

        # Publishing is idempotent by (name, version): a second publish of the
        # same definition returns the existing workflow rather than creating a
        # duplicate, so a user that restarts mid-run does not fork the workload.
        with self.client.post(
            "/api/v1/workflows/",
            json=WORKFLOW_DEFINITION,
            headers=self._headers(),
            name="publish workflow",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                self.workflow_ready = True
            else:
                response.failure(f"publish returned {response.status_code}")

    def _headers(self) -> dict[str, str]:
        """The auth header, or nothing when authentication failed."""
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    @task
    def trigger_and_wait(self) -> None:
        """Trigger one run and follow it to a terminal state."""
        if not self.token or not self.workflow_ready:
            # A failure in on_start: stop this user rather than hammer a broken
            # endpoint and report the resulting failures as a load result.
            self.stop()
            return

        # A unique idempotency key per attempt, so a Locust-level retry is a
        # replay rather than a second run — the same contract a real client
        # relies on, and the reason the key must not be a constant.
        idempotency_key = uuid.uuid4().hex

        started = time.perf_counter()
        with self.client.post(
            "/api/v1/runs/",
            json={"name": WORKFLOW_NAME, "version": 1},
            headers={**self._headers(), "Idempotency-Key": idempotency_key},
            name="trigger run",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"trigger returned {response.status_code}")
                return
            run_id = response.json()["id"]
        _record("trigger", time.perf_counter() - started)

        self._poll_until_terminal(run_id, started)

    def _poll_until_terminal(self, run_id: str, started: float) -> None:
        """Poll a run until it settles, recording the end-to-end latency."""
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        state = "pending"
        while time.monotonic() < deadline:
            with self.client.get(
                f"/api/v1/runs/{run_id}/",
                headers=self._headers(),
                name="poll run",
                catch_response=True,
            ) as response:
                if response.status_code != 200:
                    response.failure(f"poll returned {response.status_code}")
                    return
                state = response.json().get("state", "unknown")

            if state in TERMINAL_STATES:
                break
            time.sleep(POLL_INTERVAL_SECONDS)

        elapsed = time.perf_counter() - started
        if state not in TERMINAL_STATES:
            _record("run end-to-end", elapsed, TimeoutError(f"run stuck in {state!r}"))
        elif state != "succeeded":
            _record("run end-to-end", elapsed, RuntimeError(f"run ended {state!r}"))
        else:
            _record("run end-to-end", elapsed)
