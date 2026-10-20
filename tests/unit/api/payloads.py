"""Payload builders for the API tests.

Kept out of ``conftest.py`` so tests can import them directly; ``conftest`` is
not a normal module and importing from it depends on pytest's import mode.
"""

from __future__ import annotations

from typing import Any


def definition_payload(
    *,
    name: str = "etl",
    version: int = 1,
    description: str = "",
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A minimal valid definition payload.

    Two tasks, one depending on the other, so a trigger exercises both initial
    states (``READY`` and ``PENDING``) rather than only the trivial one.
    """
    if tasks is None:
        tasks = [
            {"key": "extract", "task_type": "extract"},
            {"key": "load", "task_type": "load", "depends_on": ["extract"]},
        ]
    return {"name": name, "version": version, "description": description, "tasks": tasks}
