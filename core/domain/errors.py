"""Domain errors.

These are deliberately independent of Django and of HTTP. The domain layer is
used by a management command, a worker process and a test alike, and none of
those should need a framework to describe why a transition was refused.
"""

from __future__ import annotations

from collections.abc import Iterable


class DomainError(Exception):
    """Base class for every error the domain layer raises."""


class IllegalTransitionError(DomainError):
    """A transition was attempted that the transition table does not contain.

    This is a programming error, not a runtime condition: it means the caller
    asked for something the state machine forbids. A *legal* transition that
    loses a race is not an error — the repository returns ``False`` for that.
    """

    def __init__(
        self,
        *,
        machine: str,
        source: str,
        target: str,
        allowed: Iterable[str],
    ) -> None:
        self.machine = machine
        self.source = source
        self.target = target
        self.allowed = tuple(sorted(allowed))
        if self.allowed:
            hint = f"allowed from {source!r}: {', '.join(self.allowed)}"
        else:
            hint = f"{source!r} is terminal and has no outgoing transitions"
        super().__init__(f"{machine}: cannot go from {source!r} to {target!r} ({hint})")


class LeaseRequiredError(DomainError):
    """A transition requires the caller to hold the lease and it did not."""


class InvariantViolationError(DomainError):
    """An entity was constructed in a state its invariants forbid.

    The database enforces the same rules with CHECK constraints. This exists so
    the failure surfaces as a domain error at the point of construction rather
    than as an ``IntegrityError`` from whichever query happened to write first.
    """


class DefinitionError(DomainError):
    """A workflow definition cannot be used as written.

    This is a *validation* error: it means the submitted definition is malformed
    or internally inconsistent. It is raised before anything is persisted, so a
    rejected definition never leaves a half-built `Workflow` row behind.
    """


class DuplicateTaskKeyError(DefinitionError):
    """Two tasks in one definition share a key."""

    def __init__(self, key: str, source: str = "") -> None:
        self.key = key
        where = f" (declared in {source})" if source else ""
        super().__init__(f"duplicate task key {key!r}{where}")


class UnknownDependencyError(DefinitionError):
    """A task names a dependency that is not a task in the definition."""

    def __init__(self, key: str, dependency: str) -> None:
        self.key = key
        self.dependency = dependency
        super().__init__(f"task {key!r} depends on unknown task {dependency!r}")


class CyclicDependencyError(DefinitionError):
    """The dependency graph contains a cycle.

    ``cycle`` holds the nodes of one cycle in dependency order, so the message
    renders as a path that closes on itself.

    An empty tuple is accepted. ``DependencyGraph.levels`` raises this error as a
    defensive guard and cannot always name the cycle, and indexing ``cycle[0]``
    unconditionally would have turned that guard into an ``IndexError`` — a
    confusing failure raised from inside the error constructor.
    """

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        path = " -> ".join((*cycle, cycle[0])) if cycle else "(cycle could not be named)"
        super().__init__(f"dependency cycle detected: {path}")
