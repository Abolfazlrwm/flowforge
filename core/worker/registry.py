"""A trusted handler registry.

A dispatch message carries only a ``task_type`` string. The worker looks it up in
this registry, which is populated at import time from known, in-process handlers.
Nothing in a message can cause arbitrary code to run: an unknown ``task_type`` is a
task failure, not an import. That is the whole security model — the broker is not a
code-loading channel.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

#: A handler takes the task's config mapping. A non-``None`` return value is
#: persisted into ``TaskRun.result`` when the task succeeds (Phase 6), and is the
#: value an idempotent retry reuses instead of executing again.
Handler = Callable[[Mapping[str, Any]], Any]

_HANDLERS: dict[str, Handler] = {}


def register(task_type: str, handler: Handler) -> None:
    """Register ``handler`` for ``task_type``.

    Raises:
        ValueError: if ``task_type`` is blank or already registered. A duplicate is
            a programming error, not a silent override.
    """
    if not task_type.strip():
        raise ValueError("task_type must not be blank")
    if task_type in _HANDLERS:
        raise ValueError(f"a handler for {task_type!r} is already registered")
    _HANDLERS[task_type] = handler


def resolve(task_type: str) -> Handler | None:
    """Return the trusted handler for ``task_type``, or ``None`` if none is known."""
    return _HANDLERS.get(task_type)


def handlers() -> Mapping[str, Handler]:
    """A read-only view of the registry, for diagnostics."""
    return MappingProxyType(_HANDLERS)
