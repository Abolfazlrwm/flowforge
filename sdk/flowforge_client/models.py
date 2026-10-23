"""Typed views of the API's responses.

Frozen dataclasses rather than raw dicts: a caller that writes
``run.task_runs[0].state`` gets an editor completion and an ``AttributeError``
on a typo, instead of a ``KeyError`` at runtime. ``from_payload`` is explicit
about which keys it reads, so a field the server adds is ignored rather than
breaking the parse, and a field it removes is a ``KeyError`` at parse time
instead of ``None`` three layers down.

Timestamps stay as ``datetime`` (parsed from ISO 8601) rather than strings, so a
caller can subtract two of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Run states that end a run. Anything else is still in flight.
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "cancelled"})

#: Task-run states that end a task.
TERMINAL_TASK_STATES = frozenset({"succeeded", "skipped", "dead", "cancelled"})


def _datetime(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp, or ``None``.

    ``datetime.fromisoformat`` accepts a trailing ``Z`` from Python 3.11, which
    is the floor this package declares, so no third-party parser is needed.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


@dataclass(frozen=True, slots=True)
class Task:
    """One task of a published definition.

    ``config`` is redacted by the server: a task may carry a credential to
    execute, and the API does not return it to a reader.
    """

    key: str
    task_type: str
    depends_on: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Task:
        """Build a task from an API object."""
        return cls(
            key=str(payload["key"]),
            task_type=str(payload["task_type"]),
            depends_on=tuple(payload.get("depends_on") or ()),
            config=dict(payload.get("config") or {}),
        )


@dataclass(frozen=True, slots=True)
class Workflow:
    """A published workflow definition."""

    id: str
    name: str
    version: int
    description: str = ""
    created_at: datetime | None = None
    tasks: tuple[Task, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Workflow:
        """Build a workflow from an API object."""
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            version=int(payload["version"]),
            description=str(payload.get("description") or ""),
            created_at=_datetime(payload.get("created_at")),
            tasks=tuple(Task.from_payload(task) for task in payload.get("tasks") or ()),
        )


@dataclass(frozen=True, slots=True)
class WorkflowSummary:
    """A workflow in a list, without its tasks."""

    id: str
    name: str
    version: int
    description: str = ""
    created_at: datetime | None = None
    task_count: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> WorkflowSummary:
        """Build a summary from an API object."""
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            version=int(payload["version"]),
            description=str(payload.get("description") or ""),
            created_at=_datetime(payload.get("created_at")),
            task_count=int(payload.get("task_count") or 0),
        )


@dataclass(frozen=True, slots=True)
class TaskRun:
    """One task's progress within a run."""

    id: str
    task_key: str
    state: str
    attempt: int = 1
    fencing_token: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether this task will not change state again on its own."""
        return self.state in TERMINAL_TASK_STATES

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TaskRun:
        """Build a task run from an API object."""
        return cls(
            id=str(payload["id"]),
            task_key=str(payload["task_key"]),
            state=str(payload["state"]),
            attempt=int(payload.get("attempt") or 1),
            fencing_token=int(payload.get("fencing_token") or 0),
            lease_owner=payload.get("lease_owner"),
            lease_expires_at=_datetime(payload.get("lease_expires_at")),
            created_at=_datetime(payload.get("created_at")),
            started_at=_datetime(payload.get("started_at")),
            finished_at=_datetime(payload.get("finished_at")),
        )


@dataclass(frozen=True, slots=True)
class Run:
    """A workflow run, with the state of each of its tasks."""

    id: str
    workflow_name: str
    workflow_version: int
    state: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    task_runs: tuple[TaskRun, ...] = ()

    @property
    def is_terminal(self) -> bool:
        """Whether this run has settled.

        A run that is still ``pending`` or ``running`` will change state on its
        own; a terminal one will not.
        """
        return self.state in TERMINAL_RUN_STATES

    @property
    def succeeded(self) -> bool:
        """Whether this run finished successfully."""
        return self.state == "succeeded"

    def task_run(self, task_key: str) -> TaskRun:
        """Return the task run for ``task_key``.

        Raises:
            KeyError: if the run has no such task. A typo should not look like a
                task that has not started.

        """
        for task_run in self.task_runs:
            if task_run.task_key == task_key:
                return task_run
        raise KeyError(f"run {self.id} has no task {task_key!r}")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Run:
        """Build a run from an API object."""
        return cls(
            id=str(payload["id"]),
            workflow_name=str(payload["workflow_name"]),
            workflow_version=int(payload["workflow_version"]),
            state=str(payload["state"]),
            created_at=_datetime(payload.get("created_at")),
            started_at=_datetime(payload.get("started_at")),
            finished_at=_datetime(payload.get("finished_at")),
            task_runs=tuple(
                TaskRun.from_payload(task_run) for task_run in payload.get("task_runs") or ()
            ),
        )


@dataclass(frozen=True, slots=True)
class RunSummary:
    """A run in a list, without its task runs."""

    id: str
    workflow_name: str
    workflow_version: int
    state: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether this run has settled."""
        return self.state in TERMINAL_RUN_STATES

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RunSummary:
        """Build a run summary from an API object."""
        return cls(
            id=str(payload["id"]),
            workflow_name=str(payload["workflow_name"]),
            workflow_version=int(payload["workflow_version"]),
            state=str(payload["state"]),
            created_at=_datetime(payload.get("created_at")),
            started_at=_datetime(payload.get("started_at")),
            finished_at=_datetime(payload.get("finished_at")),
        )


@dataclass(frozen=True, slots=True)
class Event:
    """One entry in a task run's append-only event log."""

    id: str
    task_key: str
    kind: str
    from_state: str | None
    to_state: str | None
    created_at: datetime | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Event:
        """Build an event from an API object."""
        return cls(
            id=str(payload["id"]),
            task_key=str(payload.get("task_key") or ""),
            kind=str(payload["kind"]),
            from_state=payload.get("from_state"),
            to_state=payload.get("to_state"),
            created_at=_datetime(payload.get("created_at")),
        )


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a cursor-paginated list.

    The API returns DRF cursor pagination: ``results`` plus ``next``/``previous``
    URLs. The URLs are kept verbatim and passed back to the client, so the client
    never has to reconstruct a cursor.
    """

    results: tuple[Any, ...]
    next_url: str | None = None
    previous_url: str | None = None

    @property
    def has_next(self) -> bool:
        """Whether another page exists."""
        return bool(self.next_url)
