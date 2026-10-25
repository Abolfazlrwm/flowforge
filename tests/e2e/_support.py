"""Shared helpers for the end-to-end suite.

These tests are black-box: they drive a *running* FlowForge deployment over
HTTP, exactly as a user's script would. They import no Django, touch no ORM, and
use no test database -- the system under test is the compose stack, not the code
in-process. That is what separates this suite from ``tests/unit`` and
``tests/integration``, and why it can only run where a stack is reachable.

The Python SDK is a separate distribution the application does not install; it
is placed on ``sys.path`` here so the suite exercises the real client and CLI.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

#: Absolute path to the sibling SDK distribution. The package ``__init__`` puts it
#: on ``sys.path``; the CLI test also needs it for the subprocess ``PYTHONPATH``.
SDK_DIR = str(Path(__file__).resolve().parents[2] / "sdk")

DEFAULT_BASE_URL = "http://localhost:8000"
#: The states a workflow run can settle in; anything else means "still working".
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "cancelled", "dead"})


def base_url() -> str:
    """Return the root URL of the deployment under test (no ``/api/v1`` suffix)."""
    return os.environ.get("FLOWFORGE_E2E_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def token() -> str:
    """Return the bearer token, or an empty string when the suite should skip."""
    return os.environ.get("FLOWFORGE_E2E_TOKEN", "").strip()


def unique_name(prefix: str) -> str:
    """Return a workflow name unique to this run, so repeated runs never collide."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sample_definition(name: str) -> dict[str, Any]:
    """Return a small diamond workflow (seed -> sum -> done) for the journey."""
    return {
        "name": name,
        "version": 1,
        "description": "The end-to-end suite's sample workflow.",
        "tasks": [
            {"key": "seed", "task_type": "echo", "config": {"value": "hello"}},
            {
                "key": "sum",
                "task_type": "add",
                "depends_on": ["seed"],
                "config": {"values": [1, 2, 3]},
            },
            {"key": "done", "task_type": "noop", "depends_on": ["sum"]},
        ],
    }


def _decode(response: Any) -> Any:
    """Decode a JSON (or plain-text) HTTP response body."""
    raw = response.read()
    if not raw:
        return None
    content_type = response.headers.get("Content-Type", "")
    if content_type and "json" not in content_type:
        return raw.decode(errors="replace")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode(errors="replace")


def request(
    method: str,
    path: str,
    *,
    tok: str,
    body: dict[str, Any] | None = None,
    root: str | None = None,
) -> tuple[int, Any]:
    """Make one REST call under ``/api/v1`` and return ``(status, body)``.

    A non-2xx response is returned rather than raised, so a test can assert on
    the status code directly. ``path`` is relative to the API root (``/runs/``).
    """
    url = f"{(root or base_url()).rstrip('/')}/api/v1{path}"
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if tok:
        headers["Authorization"] = f"Token {tok}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, _decode(response)
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc)


def wait_for_run(
    run_id: str,
    *,
    tok: str,
    root: str | None = None,
    timeout: float = 150.0,
    interval: float = 1.0,
) -> dict[str, Any]:
    """Poll a run until it settles and return the final run payload."""
    deadline = time.monotonic() + timeout
    payload: dict[str, Any] = {}
    while True:
        status, payload = request("GET", f"/runs/{run_id}/", tok=tok, root=root)
        assert status == 200, (status, payload)
        state = payload.get("state")
        if state in TERMINAL_RUN_STATES:
            return payload
        if time.monotonic() >= deadline:
            raise TimeoutError(f"run {run_id} did not settle (last state: {state})")
        time.sleep(interval)
