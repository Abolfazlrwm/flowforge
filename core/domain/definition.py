"""Workflow definitions and the dependency graph over them.

A definition is the *template*: which tasks run, in what order, and what each
one is configured to do. It is distinct from a run, which is what the rest of
`core.domain` describes.

The graph is validated when it is built, not when a task is scheduled. A
definition that references a task it does not define, or that depends on itself
in a loop, is rejected at parse time — the failure is reported to whoever wrote
the definition, while it is still cheap to fix.

Ordering is deliberately **deterministic**. `topological_order` and `levels`
both break ties by ascending task key, so the same definition always produces
the same schedule regardless of dict iteration order, Python version, or how the
definition arrived (JSON payload, database row, test fixture). Without that
property, two schedulers could disagree about which ready task to claim first
and the run would be non-reproducible.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType
from typing import Any

from core.domain.errors import (
    CyclicDependencyError,
    DefinitionError,
    DuplicateTaskKeyError,
    UnknownDependencyError,
)
from core.domain.expressions import ExpressionError, compile_expression

_EMPTY_CONFIG: Mapping[str, Any] = MappingProxyType({})


def _freeze(value: Any) -> Any:
    """Recursively convert JSON-shaped data into hashable form.

    Configuration arrives as parsed JSON, which is dicts and lists — neither is
    hashable. This makes a definition hashable so it can be used as a dict key
    for deduplication, while raising `TypeError` for anything that is not
    JSON-shaped rather than silently accepting it.
    """
    if isinstance(value, Mapping):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"{type(value).__name__} is not valid JSON configuration")


@dataclass(frozen=True, eq=True)
class TaskDefinition:
    """One task in a workflow definition.

    Args:
        key: Stable name for this task *within its workflow*. Referenced by
            other tasks' ``depends_on`` and by the scheduler, so it must be
            unique and immutable.
        task_type: Names the handler that will execute this task. The mapping
            from ``task_type`` to code is a Phase 5 concern; the definition only
            records the name.
        depends_on: Keys of tasks that must succeed before this one becomes
            eligible. Order is irrelevant — it is a set dependency, so a
            caller-supplied order does not change the schedule.
        config: Handler-specific parameters, as parsed JSON. Stored immutably
            so a definition cannot be mutated after validation.
        when: Optional condition over the run's task results. When it evaluates
            to false the task is ``SKIPPED`` rather than run. Parsed at
            construction, so a malformed condition cannot reach the scheduler.
        join: ``"all"`` (default) requires every dependency to succeed;
            ``"any"`` makes the task eligible once *one* dependency succeeds,
            which is what a fan-in over alternative branches needs.
    """

    key: str
    task_type: str
    depends_on: tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default=_EMPTY_CONFIG)
    when: str | None = None
    join: str = "all"

    def __post_init__(self) -> None:
        """Validate the identity fields and freeze the configuration in place."""
        if not self.key.strip():
            raise DefinitionError("task key must not be blank")
        if not self.task_type.strip():
            raise DefinitionError(f"task {self.key!r} has no task_type")
        if self.join not in ("all", "any"):
            raise DefinitionError(
                f"task {self.key!r} has join={self.join!r}; expected 'all' or 'any'"
            )
        if self.join == "any" and not self.depends_on:
            raise DefinitionError(
                f"task {self.key!r} uses join='any' but declares no dependencies; "
                "there is nothing to join"
            )
        if self.when is not None:
            try:
                compile_expression(self.when)
            except ExpressionError as exc:
                # Re-raised as a definition error so the API reports it as a
                # validation failure against the task, not as an internal error.
                raise DefinitionError(f"task {self.key!r} has an invalid when: {exc}") from None
        seen: set[str] = set()
        for dependency in self.depends_on:
            if not dependency.strip():
                raise DefinitionError(f"task {self.key!r} has a blank dependency")
            if dependency in seen:
                raise DefinitionError(f"task {self.key!r} lists dependency {dependency!r} twice")
            seen.add(dependency)
        # Normalise so a caller cannot hand us a shared, mutable dict and then
        # change the definition out from under the validator.
        if not isinstance(self.config, MappingProxyType):
            object.__setattr__(self, "config", MappingProxyType(dict(self.config)))
        # Force the freeze here rather than leaving it to ``__hash__`` and
        # ``fingerprint()``, which are the only other callers. Otherwise a
        # config value that is not JSON-shaped passes construction and surfaces
        # later as a ``TypeError`` from ``hash``, from ``fingerprint``, or from
        # ``JSONField`` on save — none of which name the task that caused it.
        _freeze(self.config)

    def __hash__(self) -> int:
        """Hash on identity, which includes the frozen configuration."""
        return hash((self.key, self.task_type, _freeze(self.config), self.when, self.join))


@dataclass(frozen=True, eq=True)
class WorkflowDefinition:
    """A versioned workflow definition.

    ``(name, version)`` identifies it the same way the database does. Two
    definitions with the same name and version but different content are the
    same definition; differing content is a publishing error, not a second
    definition.
    """

    name: str
    version: int
    tasks: tuple[TaskDefinition, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        """Validate the definition's own name and version."""
        if not self.name.strip():
            raise DefinitionError("workflow name must not be blank")
        if self.version < 1:
            raise DefinitionError(f"workflow version must be >= 1, got {self.version}")

    @cached_property
    def graph(self) -> DependencyGraph:
        """The validated dependency graph over this definition's tasks."""
        return DependencyGraph(self.tasks)

    def task(self, key: str) -> TaskDefinition:
        """Look up one task by key.

        Raises:
            UnknownDependencyError: if no task has that key.
        """
        for candidate in self.tasks:
            if candidate.key == key:
                return candidate
        raise UnknownDependencyError(key, key)

    @property
    def task_keys(self) -> tuple[str, ...]:
        """Every task key, in declaration order."""
        return tuple(task.key for task in self.tasks)

    def fingerprint(self) -> str:
        """A stable digest of this definition's *content*.

        Two definitions with the same fingerprint are interchangeable: same name,
        same version, same tasks, same dependencies, same configuration, same
        description. This is what a caller checks before republishing, so
        re-posting an identical definition is a no-op instead of a second row.

        Description is included deliberately. Leaving it out would mean
        publishing ``etl v1`` with a different description returns the stored row
        and silently ignores the new text — the caller believes one thing and
        the database holds another, which is the same failure the version
        constraint prevents for tasks.
        """
        payload = "|".join(
            (
                self.name,
                str(self.version),
                self.description,
                *(
                    f"{t.key}:{t.task_type}:{sorted(t.depends_on)}:{_freeze(t.config)!r}"
                    f":{t.when!r}:{t.join}"
                    for t in self.tasks
                ),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _adjacency(tasks: Iterable[TaskDefinition]) -> dict[str, tuple[str, ...]]:
    """Build ``task -> its dependencies`` as sorted tuples.

    Sorting here is what makes every downstream traversal deterministic without
    each caller remembering to sort again.
    """
    result: dict[str, tuple[str, ...]] = {}
    for task in tasks:
        result[task.key] = tuple(sorted(set(task.depends_on)))
    return result


class DependencyGraph:
    """The dependency graph of one definition.

    Constructing it validates the graph: duplicate keys, references to tasks
    that do not exist, and cycles are all rejected here, once, rather than being
    rediscovered by whichever query happens to run first.

    The class is deliberately not frozen — it caches derived views. Treat every
    instance as immutable; nothing here mutates after construction.
    """

    def __init__(self, tasks: Iterable[TaskDefinition]) -> None:
        self._tasks: tuple[TaskDefinition, ...] = tuple(tasks)
        self._by_key: dict[str, TaskDefinition] = {}
        for task in self._tasks:
            existing = self._by_key.get(task.key)
            if existing is not None:
                raise DuplicateTaskKeyError(task.key)
            self._by_key[task.key] = task

        self._dependencies = _adjacency(self._tasks)
        for key, dependencies in self._dependencies.items():
            for dependency in dependencies:
                if dependency not in self._by_key:
                    raise UnknownDependencyError(key, dependency)

        cycle = self._find_cycle()
        if cycle is not None:
            raise CyclicDependencyError(cycle)

    # -- basic accessors ---------------------------------------------------

    def __len__(self) -> int:
        """The number of tasks in this graph."""
        return len(self._tasks)

    def __contains__(self, key: object) -> bool:
        """Whether this graph contains a task with that key."""
        return key in self._by_key

    @property
    def keys(self) -> tuple[str, ...]:
        """Every task key, in declaration order."""
        return tuple(task.key for task in self._tasks)

    @property
    def tasks(self) -> tuple[TaskDefinition, ...]:
        """Every task, in declaration order."""
        return self._tasks

    def task(self, key: str) -> TaskDefinition:
        """The :class:`TaskDefinition` for ``key``.

        Raises:
            UnknownDependencyError: if no task has that key.
        """
        try:
            return self._by_key[key]
        except KeyError:
            raise UnknownDependencyError(key, key) from None

    def dependencies_of(self, key: str) -> frozenset[str]:
        """Tasks that must finish before ``key`` may start."""
        self.task(key)
        return frozenset(self._dependencies[key])

    def dependents_of(self, key: str) -> frozenset[str]:
        """Tasks that wait on ``key``."""
        self.task(key)
        return frozenset(candidate for candidate, deps in self._dependencies.items() if key in deps)

    # -- ordering ----------------------------------------------------------

    def topological_order(self) -> tuple[str, ...]:
        """Every task in dependency order.

        Nothing in this order starts before its dependencies. Ties are broken by
        ascending key, so the result is identical for identical definitions.
        """
        return tuple(key for level in self.levels() for key in level)

    def levels(self) -> tuple[tuple[str, ...], ...]:
        """Tasks grouped into waves that can run in parallel.

        Level 0 has no dependencies; level *n* depends only on levels below it.
        The scheduler claims a whole wave at once, so this is the structure that
        decides the maximum concurrency of a run.
        """
        remaining = {key: set(deps) for key, deps in self._dependencies.items()}
        done: set[str] = set()
        waves: list[tuple[str, ...]] = []
        while remaining:
            ready = tuple(sorted(key for key, deps in remaining.items() if not deps - done))
            if not ready:
                # Unreachable: the constructor already rejected cycles.
                raise CyclicDependencyError(self._find_cycle() or ())
            waves.append(ready)
            for key in ready:
                del remaining[key]
            done.update(ready)
        return tuple(waves)

    # -- reachability ------------------------------------------------------

    def transitive_dependencies_of(self, key: str) -> frozenset[str]:
        """Everything ``key`` waits on, directly or indirectly."""
        self.task(key)
        seen: set[str] = set()
        frontier = list(self._dependencies[key])
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(self._dependencies[current])
        return frozenset(seen)

    def transitive_dependents_of(self, key: str) -> frozenset[str]:
        """Everything that waits on ``key``, directly or indirectly."""
        self.task(key)
        reverse = self._reverse()
        seen: set[str] = set()
        frontier = list(reverse.get(key, ()))
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            frontier.extend(reverse.get(current, ()))
        return frozenset(seen)

    def is_ready(self, key: str, completed: Iterable[str]) -> bool:
        """Whether ``key`` may start, given the tasks already ``completed``.

        Only successful completion counts: "started but not finished" is not
        completion, and this method deliberately takes no knowledge of task
        *state* — the caller supplies the set that finished successfully.
        """
        self.task(key)
        return not (set(self._dependencies[key]) - set(completed))

    def ready_tasks(self, completed: Iterable[str]) -> tuple[str, ...]:
        """Every task that may start given ``completed``, in schedule order.

        Tasks already listed in ``completed`` are excluded. A scheduler that
        asked "what should I start?" and was handed a finished task would run it
        a second time, which is exactly the duplicate execution this engine
        exists to prevent.

        This is the question the Phase 4 scheduler asks on every loop.
        """
        done = set(completed)
        return tuple(
            key
            for key in self.topological_order()
            # A key that is already done is skipped, and `key not in done` also
            # means an unknown *completion* entry can never shadow a real task.
            if key not in done and not (set(self._dependencies[key]) - done)
        )

    # -- internals ---------------------------------------------------------

    def _reverse(self) -> dict[str, tuple[str, ...]]:
        reverse: dict[str, list[str]] = {key: [] for key in self._dependencies}
        for key, deps in self._dependencies.items():
            for dep in deps:
                reverse[dep].append(key)
        return {key: tuple(sorted(values)) for key, values in reverse.items()}

    def _find_cycle(self) -> tuple[str, ...] | None:
        """Return one cycle as a closed path, or ``None``.

        Iterative, depth-first: a recursive walk would overflow the interpreter
        stack on a definition with a deep dependency chain, and a large
        workflow is exactly where a cycle report is hardest to produce by hand.

        Returns the nodes in dependency order, so ``cycle[0]`` depends on
        ``cycle[-1]`` and ``CyclicDependencyError`` renders it as a path that
        closes on itself.
        """
        unvisited, visiting, done = 0, 1, 2
        colour = dict.fromkeys(self._dependencies, unvisited)
        for root in sorted(self._dependencies):
            if colour[root] != unvisited:
                continue
            path: list[str] = []
            on_path: dict[str, int] = {}
            # Each frame is (node, next-neighbour-index) so the "already
            # visited" bookkeeping survives resumption without recursion.
            stack: list[tuple[str, int]] = [(root, 0)]
            while stack:
                node, index = stack[-1]
                if index == 0:
                    colour[node] = visiting
                    on_path[node] = len(path)
                    path.append(node)
                neighbours = self._dependencies[node]
                if index < len(neighbours):
                    stack[-1] = (node, index + 1)
                    neighbour = neighbours[index]
                    if neighbour in on_path:
                        return tuple(path[on_path[neighbour] :])
                    if colour[neighbour] == unvisited:
                        stack.append((neighbour, 0))
                else:
                    stack.pop()
                    colour[node] = done
                    del on_path[node]
                    path.pop()
        return None
