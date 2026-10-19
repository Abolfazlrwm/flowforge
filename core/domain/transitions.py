"""State machines as data.

A machine is a *table*, not a set of methods. That choice is deliberate:

- The table can be printed, diffed, tested exhaustively and rendered into
  documentation, which a pile of ``if state == ...`` branches cannot.
- Every illegal transition is illegal for exactly one reason — it is absent —
  so there is no path where a missing ``elif`` silently allows something.
- The table is validated when the module is imported, so a mistake fails the
  test run at collection rather than in production at 3am.

The class is generic over the state enum, so mypy rejects passing a
:class:`WorkflowRunState` where a :class:`TaskRunState` is expected.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from core.domain.errors import IllegalTransitionError, LeaseRequiredError

StateT = TypeVar("StateT", bound=StrEnum)


@dataclass(frozen=True, slots=True)
class Transition(Generic[StateT]):
    """One edge in a state machine.

    Attributes:
        name: Human-readable verb, used in logs, errors and documentation.
        source: State the transition starts from.
        target: State the transition ends in.
        requires_lease: Whether the caller must already hold the task's lease.
            Claiming a task is ``False`` (it *acquires* the lease); finishing a
            task is ``True`` (only the current lease holder may complete it, and
            the fencing token is what proves it).
    """

    name: str
    source: StateT
    target: StateT
    requires_lease: bool = False


class StateMachine(Generic[StateT]):
    """An immutable, validated transition table."""

    __slots__ = ("_by_source", "_name", "_states", "_table", "_terminal")

    def __init__(
        self,
        *,
        name: str,
        states: type[StateT],
        transitions: Iterable[Transition[StateT]],
        terminal: Iterable[StateT],
    ) -> None:
        """Build and validate a machine.

        Raises:
            ValueError: if the table is internally inconsistent. This is
                intentionally not a domain error: a malformed table is a bug in
                the source, not a condition at runtime.
        """
        table = tuple(transitions)
        terminal_set = frozenset(terminal)

        seen_edges: set[tuple[StateT, StateT]] = set()
        seen_names: set[str] = set()
        for transition in table:
            if not isinstance(transition.source, states):
                raise ValueError(
                    f"{name}: transition {transition.name!r} has a source "
                    f"{transition.source!r} that is not a {states.__name__}"
                )
            if not isinstance(transition.target, states):
                raise ValueError(
                    f"{name}: transition {transition.name!r} has a target "
                    f"{transition.target!r} that is not a {states.__name__}"
                )
            edge = (transition.source, transition.target)
            if edge in seen_edges:
                raise ValueError(
                    f"{name}: duplicate edge {transition.source!r} -> {transition.target!r}"
                )
            seen_edges.add(edge)
            if transition.name in seen_names:
                raise ValueError(f"{name}: duplicate transition name {transition.name!r}")
            seen_names.add(transition.name)

        # Note: a set difference cannot do this check. Both state enums are
        # StrEnum, so TaskRunState.CANCELLED == WorkflowRunState.CANCELLED is
        # True (both equal "cancelled") and they hash identically. Only an
        # isinstance check distinguishes them.
        for state in terminal_set:
            if not isinstance(state, states):
                raise ValueError(f"{name}: terminal state {state!r} is not a {states.__name__}")

        by_source: dict[StateT, list[Transition[StateT]]] = {state: [] for state in states}
        for transition in table:
            by_source[transition.source].append(transition)

        for state in states:
            outgoing = by_source[state]
            if state in terminal_set and outgoing:
                raise ValueError(
                    f"{name}: {state!r} is declared terminal but has outgoing transitions"
                )
            if state not in terminal_set and not outgoing:
                raise ValueError(
                    f"{name}: {state!r} is not terminal but has no outgoing transitions, "
                    "so reaching it would deadlock the run"
                )

        self._name = name
        self._states = states
        self._table = table
        self._terminal = terminal_set
        self._by_source = {state: tuple(edges) for state, edges in by_source.items()}

    @property
    def name(self) -> str:
        """Machine name, used in error messages and documentation."""
        return self._name

    @property
    def states(self) -> type[StateT]:
        """The state enum this machine operates on."""
        return self._states

    @property
    def transitions(self) -> tuple[Transition[StateT], ...]:
        """Every edge in declaration order."""
        return self._table

    @property
    def terminal(self) -> frozenset[StateT]:
        """States with no outgoing transitions."""
        return self._terminal

    def __iter__(self) -> Iterator[Transition[StateT]]:
        """Iterate edges, so ``for t in machine`` works."""
        return iter(self._table)

    def outgoing(self, source: StateT) -> tuple[Transition[StateT], ...]:
        """Every edge leaving ``source``, in declaration order."""
        return self._by_source[source]

    def targets(self, source: StateT) -> frozenset[StateT]:
        """States reachable from ``source`` in one step."""
        return frozenset(edge.target for edge in self._by_source[source])

    def sources(self, target: StateT) -> frozenset[StateT]:
        """States that can reach ``target`` in one step."""
        return frozenset(edge.source for edge in self._table if edge.target is target)

    def is_terminal(self, state: StateT) -> bool:
        """Whether ``state`` has no outgoing transitions."""
        return state in self._terminal

    def can(self, source: StateT, target: StateT) -> bool:
        """Whether ``source -> target`` is a declared edge."""
        return any(edge.target is target for edge in self._by_source[source])

    def edge(self, source: StateT, target: StateT) -> Transition[StateT]:
        """Return the edge for ``source -> target``.

        Raises:
            IllegalTransitionError: if no such edge exists.
        """
        for edge in self._by_source[source]:
            if edge.target is target:
                return edge
        raise IllegalTransitionError(
            machine=self._name,
            source=source.value,
            target=target.value,
            allowed=(t.value for t in self.targets(source)),
        )

    def apply(self, source: StateT, target: StateT, *, lease_held: bool = False) -> StateT:
        """Validate a transition and return the resulting state.

        This does not touch a database. It exists so that callers who are about
        to write can reject an illegal request before opening a transaction,
        and so the rule is testable without one.

        Raises:
            IllegalTransitionError: if ``source -> target`` is not declared.
            LeaseRequiredError: if the edge requires a held lease and none was given.
        """
        edge = self.edge(source, target)
        if edge.requires_lease and not lease_held:
            raise LeaseRequiredError(
                f"{self._name}: {edge.name!r} ({source.value} -> {target.value}) "
                "requires the caller to hold the lease"
            )
        return edge.target

    def rows(self) -> tuple[tuple[str, str, str, bool], ...]:
        """Flatten the table for documentation and snapshot tests."""
        return tuple(
            (edge.name, edge.source.value, edge.target.value, edge.requires_lease)
            for edge in self._table
        )

    def __repr__(self) -> str:
        """Debug representation that shows the shape, not the whole table."""
        return (
            f"StateMachine(name={self._name!r}, "
            f"states={len(self._states)}, edges={len(self._table)}, "
            f"terminal={sorted(s.value for s in self._terminal)})"
        )
