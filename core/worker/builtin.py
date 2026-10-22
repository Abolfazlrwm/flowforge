"""Built-in handlers.

These are the only functions a dispatch message can cause to run. They are
registered at import time, and ``manage.py worker`` imports this module so the
registry is populated before any message is consumed.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.worker.registry import register


def noop(_config: Mapping[str, Any]) -> None:
    """Do nothing. Useful for a step that exists only to sequence others."""
    return None


def echo(config: Mapping[str, Any]) -> Any:
    """Return the configured ``value`` unchanged."""
    return config.get("value")


def add(config: Mapping[str, Any]) -> float:
    """Sum the configured ``values`` list."""
    return float(sum(config.get("values", [])))


def wait_for_file(config: Mapping[str, Any]) -> bool:
    """Block until ``config['path']`` exists, or ``config['timeout']`` seconds pass.

    A demo/test handler that gives a long-running task a deterministic "release"
    signal (a file appears) instead of a timer, so a SIGTERM test can hold a task
    in flight without racing a sleep. Polling is the handler's own implementation,
    not test synchronisation.
    """
    path = Path(str(config["path"]))
    deadline = time.monotonic() + float(config.get("timeout", 30))
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return path.exists()


def fail(config: Mapping[str, Any]) -> Any:
    """Always raise. A deterministic failure for retry and saga end-to-end tests.

    A saga test needs a task that genuinely fails through the real worker, not one
    whose state is forced by a test. This raises so the worker records FAILED and
    the retry/compensation lifecycle runs exactly as it would in production.
    """
    raise RuntimeError(str(config.get("message", "fail handler invoked")))


register("noop", noop)
register("echo", echo)
register("add", add)
register("wait_for_file", wait_for_file)
register("fail", fail)
