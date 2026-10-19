"""Tests for DependencyGraph: validation, ordering and reachability.

The ordering tests assert *equality of whole tuples*, not membership, because
determinism is the point. A graph that returns the right set in a different
order is still a bug: the Phase 4 scheduler claims "the oldest ready task first",
and two workers disagreeing about which that is would produce irreproducible
runs.
"""

from __future__ import annotations

import pytest
from core.domain.definition import DependencyGraph, TaskDefinition
from core.domain.errors import (
    CyclicDependencyError,
    DuplicateTaskKeyError,
    UnknownDependencyError,
)


def _graph(*pairs: tuple[str, tuple[str, ...]]) -> DependencyGraph:
    """Build a graph from ``(key, depends_on)`` pairs."""
    return DependencyGraph([TaskDefinition(key, "task", depends_on=deps) for key, deps in pairs])


class TestValidation:
    """A graph that cannot run is refused when it is built."""

    def test_a_duplicate_key_is_refused(self) -> None:
        """Two tasks with one key cannot both be resolved by name."""
        with pytest.raises(DuplicateTaskKeyError, match="duplicate task key 'a'"):
            _graph(("a", ()), ("a", ()))

    def test_an_unknown_dependency_is_refused(self) -> None:
        """A reference to a task that does not exist can never be satisfied."""
        with pytest.raises(UnknownDependencyError, match="depends on unknown task 'ghost'"):
            _graph(("a", ("ghost",)))

    def test_a_self_dependency_is_refused_as_a_cycle(self) -> None:
        """A task depending on itself can never start.

        Reported as a cycle rather than a special case, because a self-edge *is*
        a cycle and one code path is easier to trust than two.
        """
        with pytest.raises(CyclicDependencyError, match="a -> a"):
            _graph(("a", ("a",)))

    def test_a_two_node_cycle_is_refused_and_names_the_path(self) -> None:
        """The reported cycle must be the real one, not a plausible one."""
        with pytest.raises(CyclicDependencyError) as raised:
            _graph(("a", ("b",)), ("b", ("a",)))
        assert set(raised.value.cycle) == {"a", "b"}
        assert "->" in str(raised.value)

    def test_a_cycle_among_unrelated_nodes_is_still_found(self) -> None:
        """The cycle search visits every component, not just the first root."""
        with pytest.raises(CyclicDependencyError):
            _graph(("standalone", ()), ("x", ("y",)), ("y", ("x",)))

    def test_an_unnamed_cycle_does_not_crash_the_error(self) -> None:
        """``levels`` raises this as a guard and cannot always name the cycle.

        Indexing ``cycle[0]`` unconditionally turned that guard into an
        ``IndexError`` raised from inside the error constructor, which would have
        hidden the real failure behind a confusing one.
        """
        error = CyclicDependencyError(())
        assert error.cycle == ()
        assert "cycle could not be named" in str(error)

    def test_a_self_dependency_can_be_hidden_among_valid_nodes(self) -> None:
        """A single bad edge in an otherwise fine definition is still fatal."""
        with pytest.raises(CyclicDependencyError):
            _graph(("ok", ()), ("a", ("a",)))

    def test_a_long_chain_is_not_a_cycle(self) -> None:
        """A deep chain must not be mistaken for one.

        A recursive walker overflows the interpreter stack around 1000 frames;
        the implementation is iterative precisely so that this passes for a
        chain of 3000.
        """
        pairs: list[tuple[str, tuple[str, ...]]] = [("t0", ())]
        pairs.extend((f"t{index}", (f"t{index - 1}",)) for index in range(1, 3000))
        graph = _graph(*pairs)
        assert len(graph.levels()) == 3000

    def test_a_cycle_at_the_end_of_a_deep_chain_is_found(self) -> None:
        """The pathological case for the iterative search.

        ``t0`` waits on ``tail``, which waits on ``t2999``, which waits back down
        the chain to ``t1``, which waits on ``t0``. The search has to walk 3000
        levels before it meets an edge that closes the loop.
        """
        pairs: list[tuple[str, tuple[str, ...]]] = [("t0", ())]
        pairs.extend((f"t{index}", (f"t{index - 1}",)) for index in range(1, 3000))
        pairs.append(("tail", ("t2999",)))
        # Close the loop at the very far end.
        pairs[0] = ("t0", ("tail",))
        with pytest.raises(CyclicDependencyError):
            _graph(*pairs)

    def test_a_diamond_is_accepted(self) -> None:
        """A node reachable by two paths is not a cycle."""
        graph = _graph(("a", ()), ("b", ("a",)), ("c", ("a",)), ("d", ("b", "c")))
        assert graph.topological_order() == ("a", "b", "c", "d")


class TestOrdering:
    """The schedule a definition produces, and why it is reproducible."""

    def test_topological_order_respects_dependencies(self) -> None:
        """Nothing in this order starts before its dependencies."""
        graph = _graph(("extract", ()), ("load", ("extract",)), ("notify", ("load",)))
        order = graph.topological_order()
        edges = {"load": ("extract",), "notify": ("load",)}
        for key, dependencies in edges.items():
            for dependency in dependencies:
                assert order.index(dependency) < order.index(key)

    def test_ordering_is_lexicographic_when_unconstrained(self) -> None:
        """Ties are broken by key, not by declaration order.

        This is the whole determinism contract. Both tasks are ready at once; the
        graph must always name the same one first.
        """
        graph = _graph(("zebra", ()), ("alpha", ()), ("mid", ()))
        assert graph.topological_order() == ("alpha", "mid", "zebra")

    def test_declaration_order_does_not_affect_ordering(self) -> None:
        """Two definitions differing only in task order are the same schedule."""
        first = _graph(("a", ()), ("b", ()), ("c", ("a",)))
        second = _graph(("c", ("a",)), ("b", ()), ("a", ()))
        assert first.topological_order() == second.topological_order()

    def test_dependency_list_order_does_not_affect_ordering(self) -> None:
        """``depends_on`` is a set, so its order is not part of the definition."""
        first = _graph(("a", ()), ("b", ()), ("c", ("a", "b")))
        second = _graph(("a", ()), ("b", ()), ("c", ("b", "a")))
        assert first.topological_order() == second.topological_order()

    def test_levels_group_tasks_that_can_run_together(self) -> None:
        """A wave is the unit of parallel scheduling."""
        graph = _graph(
            ("extract", ()),
            ("transform", ("extract",)),
            ("load", ("extract",)),
            ("notify", ("load",)),
        )
        assert graph.levels() == (
            ("extract",),
            ("load", "transform"),
            ("notify",),
        )

    def test_every_task_appears_exactly_once_across_levels(self) -> None:
        """A key in two waves would be scheduled twice."""
        graph = _graph(
            ("a", ()),
            ("b", ("a",)),
            ("c", ("a",)),
            ("d", ("b", "c")),
            ("e", ("d",)),
        )
        flat = [key for level in graph.levels() for key in level]
        assert sorted(flat) == ["a", "b", "c", "d", "e"]

    def test_no_task_shares_a_level_with_its_own_dependency(self) -> None:
        """If it did, a wave could start before the thing it waits on."""
        graph = _graph(("a", ()), ("b", ("a", "z")), ("c", ("b",)), ("z", ()))
        for level_index, level in enumerate(graph.levels()):
            for key in level:
                assert not set(graph.dependencies_of(key)) & set(level)
                earlier = [k for wave in graph.levels()[:level_index] for k in wave]
                assert set(graph.dependencies_of(key)) <= set(earlier)

    def test_a_single_task_is_its_own_first_level(self) -> None:
        """A one-task workflow is a single wave."""
        assert _graph(("only", ())).levels() == (("only",),)

    def test_an_empty_graph_has_no_levels(self) -> None:
        """No tasks means nothing to schedule, not an error."""
        assert _graph().levels() == ()


class TestReadiness:
    """The question the Phase 4 scheduler asks every loop."""

    def _graph(self) -> DependencyGraph:
        """A three-wave graph with one root and two independent children."""
        return _graph(
            ("extract", ()),
            ("transform_a", ("extract",)),
            ("transform_b", ("extract",)),
            ("load", ("transform_a", "transform_b")),
        )

    def test_nothing_with_dependencies_is_ready_before_anything_completes(self) -> None:
        """Only unblocked work is eligible, before anything finishes."""
        assert self._graph().ready_tasks(completed=[]) == ("extract",)

    def test_both_children_become_ready_when_the_parent_completes(self) -> None:
        """A wave opens all at once, not one child per loop."""
        assert self._graph().ready_tasks(completed=["extract"]) == ("transform_a", "transform_b")

    def test_a_completed_task_is_not_returned_as_ready(self) -> None:
        """The bug this test guards against.

        A scheduler that is told to start a task that already finished runs it
        twice, which is the whole duplicate-execution problem this engine exists
        to prevent.
        """
        assert self._graph().ready_tasks(completed=["extract"]) == ("transform_a", "transform_b")

    def test_a_task_is_not_ready_until_every_dependency_completes(self) -> None:
        """Partial completion is not completion."""
        graph = self._graph()
        assert graph.is_ready("load", completed=["extract", "transform_a"]) is False
        assert graph.is_ready("load", completed=["extract", "transform_a", "transform_b"]) is True

    def test_an_unknown_completion_is_not_an_error(self) -> None:
        """Completions outside the graph are noise, not a fault.

        A retry worker may report a task the definition no longer contains
        after a version change; treating that as an error would break the
        scheduler on every restart.
        """
        assert self._graph().ready_tasks(completed=["extract", "from_an_older_version"]) == (
            "transform_a",
            "transform_b",
        )

    def test_ready_tasks_is_ordered(self) -> None:
        """Readiness is reported in schedule order, so claims are reproducible."""
        assert self._graph().ready_tasks(completed=["extract"]) == ("transform_a", "transform_b")

    def test_nothing_is_ready_once_everything_is_done(self) -> None:
        """A drained graph has no work left to offer."""
        graph = self._graph()
        assert graph.ready_tasks(set(graph.keys)) == ()


class TestReachability:
    """What a task waits on, and what waits on it."""

    def _graph(self) -> DependencyGraph:
        """A chain with one shortcut, so both kinds of edge exist."""
        return _graph(
            ("extract", ()),
            ("transform", ("extract",)),
            ("load", ("transform",)),
            ("notify", ("load", "transform")),
        )

    def test_transitive_dependencies(self) -> None:
        """Everything a task waits on, directly or indirectly."""
        assert self._graph().transitive_dependencies_of("notify") == {
            "extract",
            "transform",
            "load",
        }

    def test_transitive_dependencies_of_an_unconnected_task_is_empty(self) -> None:
        """A task with no dependencies reaches nothing."""
        assert self._graph().transitive_dependencies_of("extract") == frozenset()

    def test_transitive_dependents(self) -> None:
        """Everything that would be blocked if this task never finished."""
        assert self._graph().transitive_dependents_of("extract") == {
            "transform",
            "load",
            "notify",
        }

    def test_transitive_dependents_of_a_sink_is_empty(self) -> None:
        """A sink blocks nothing."""
        assert self._graph().transitive_dependents_of("notify") == frozenset()

    def test_a_diamond_is_traversed_once(self) -> None:
        """``notify`` reaches ``extract`` through both ``load`` and ``transform``."""
        graph = _graph(("a", ()), ("b", ("a",)), ("c", ("a",)), ("d", ("b", "c")))
        assert graph.transitive_dependencies_of("d") == {"a", "b", "c"}
        assert graph.transitive_dependents_of("a") == {"b", "c", "d"}

    def test_unknown_keys_are_refused(self) -> None:
        """A typo fails here, not silently as an empty answer."""
        graph = self._graph()
        with pytest.raises(UnknownDependencyError):
            graph.transitive_dependencies_of("nope")
        with pytest.raises(UnknownDependencyError):
            graph.transitive_dependents_of("nope")
        with pytest.raises(UnknownDependencyError):
            graph.is_ready("nope", completed=[])

    def test_dependencies_and_dependents_are_inverses(self) -> None:
        """Each edge appears once in each direction."""
        graph = self._graph()
        assert graph.dependencies_of("notify") == {"load", "transform"}
        assert graph.dependents_of("transform") == {"notify", "load"}


class TestContainerProtocol:
    """The graph behaves like a well-mannered mapping."""

    def _graph(self) -> DependencyGraph:
        """Two independent tasks, declared out of alphabetical order."""
        return _graph(("c", ()), ("a", ()))

    def test_contains(self) -> None:
        """Membership is by key, the way callers will use it."""
        assert "a" in self._graph()
        assert "b" not in self._graph()

    def test_len(self) -> None:
        """Length is the task count."""
        assert len(self._graph()) == 2

    def test_keys(self) -> None:
        """Keys are reported in declaration order."""
        assert self._graph().keys == ("c", "a")

    def test_task_lookup(self) -> None:
        """A lookup returns the definition, not just a name."""
        graph = _graph(("a", ("b",)), ("b", ()))
        assert graph.task("a").key == "a"

    def test_tasks_are_returned_in_declaration_order(self) -> None:
        """The graph keeps what the author wrote."""
        graph = self._graph()
        assert [task.key for task in graph.tasks] == ["c", "a"]
