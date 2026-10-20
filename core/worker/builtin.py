"""Built-in handlers.

These are the only functions a dispatch message can cause to run. They are
registered at import time, and ``manage.py worker`` imports this module so the
registry is populated before any message is consumed.
"""

from __future__ import annotations

from collections.abc import Mapping
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


register("noop", noop)
register("echo", echo)
register("add", add)
