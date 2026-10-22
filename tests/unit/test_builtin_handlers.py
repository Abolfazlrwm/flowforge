"""The built-in handlers the compose end-to-end commands rely on.

These are trivial, but the saga end-to-end command depends on ``fail`` actually
raising — a regression that made it a no-op would silently turn the saga smoke
test green without ever exercising a real failure. This pins that down.
"""

from __future__ import annotations

import pytest
from core.worker import builtin  # noqa: F401 - importing populates the registry
from core.worker.registry import resolve


def test_the_fail_handler_always_raises() -> None:
    """``fail`` is the deterministic failure the saga end-to-end run needs."""
    handler = resolve("fail")
    assert handler is not None
    with pytest.raises(RuntimeError, match="declined"):
        handler({"message": "charge declined"})


def test_every_compose_handler_is_registered() -> None:
    """The handlers the end-to-end commands and samples reference all resolve."""
    for task_type in ("echo", "add", "noop", "fail", "wait_for_file"):
        assert resolve(task_type) is not None, f"{task_type} is not registered"
