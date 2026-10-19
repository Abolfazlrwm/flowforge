"""Property-based tests for the dependency graph.

The unit tests assert that specific graphs behave. These assert that *every*
graph does, which is a different claim — the tables in Phase 1 were tested the
same way for the same reason.

Two strategies are used, and the difference matters:

``dags()`` builds **valid** graphs by construction — each task may only depend on
a task declared before it — so acyclicity and reference-existence are guaranteed
and the invariants under test are the ones a scheduler relies on.

``arbitrary_graphs()`` builds **possibly-broken** graphs, to check that
`DependencyGraph` never accepts a cycle and never invents one. Every graph
Hypothesis can build must either validate or be refused with a real cycle.
"""

from __future__ import annotations

import pytest
from core.domain.definition import DependencyGraph, TaskDefinition, WorkflowDefinition
from core.domain.errors import CyclicDependencyError, UnknownDependencyError
from hypothesis import given, settings
from hypothesis import strategies as st

#: A drawn graph: ``(key, dependencies)`` pairs, as produced by the strategies
#: above. Aliased because every test below takes one and the spelled-out type
#: is long enough to break the line-length limit on its own.
Raw = list[tuple[str, tuple[str, ...]]]


def _build(raw: Raw) -> DependencyGraph:
    return DependencyGraph([TaskDefinition(key, "task", depends_on=deps) for key, deps in raw])


@st.composite
def dags(draw: st.DrawFn) -> list[tuple[str, tuple[str, ...]]]:
    """Draw an acyclic graph whose every reference resolves.

    Task ``t{i}`` may only depend on ``t{<i}``, which makes a cycle structurally
    impossible. This is the same construction a hand-written DAG test would use,
    except the sizes and edge sets are random.

    ``t0`` has no candidates, so its dependencies are drawn from the empty
    list rather than sampled from it — ``sampled_from([])`` raises rather than
    yielding ``()``.
    """
    size = draw(st.integers(min_value=1, max_value=7))
    tasks: list[tuple[str, tuple[str, ...]]] = []
    for index in range(size):
        available = [f"t{earlier}" for earlier in range(index)]
        if available:
            dependencies = draw(st.lists(st.sampled_from(available), max_size=4, unique=True))
        else:
            dependencies = []
        tasks.append((f"t{index}", tuple(sorted(dependencies))))
    return tasks


@st.composite
def arbitrary_graphs(draw: st.DrawFn) -> list[tuple[str, tuple[str, ...]]]:
    """Draw any graph at all, cycles and dangling references included."""
    keys = draw(st.lists(_KEYS, min_size=1, max_size=6, unique=True))
    return [
        (
            key,
            tuple(
                sorted(
                    draw(
                        st.lists(
                            _KEYS,
                            max_size=3,
                            unique=True,
                        )
                    )
                )
            ),
        )
        for key in keys
    ]


#: Keys look like ``t0 .. t6``; the only constraint is that they are distinct.
_KEYS = st.integers(min_value=0, max_value=6).map(lambda n: f"t{n}")


class TestGraphInvariants:
    """Invariants that hold for every valid graph Hypothesis can build."""

    @given(dags())
    @settings(max_examples=250, deadline=None)
    def test_topological_order_is_a_permutation_of_the_keys(self, raw: Raw) -> None:
        """Nothing lost, nothing invented, nothing duplicated."""
        graph = _build(raw)
        assert sorted(graph.topological_order()) == sorted(key for key, _ in raw)

    @given(dags())
    @settings(max_examples=250, deadline=None)
    def test_dependencies_precede_dependents_in_the_order(self, raw: Raw) -> None:
        """Every dependency textually precedes the task that waits on it."""
        order = _build(raw).topological_order()
        position = {key: index for index, key in enumerate(order)}
        edges = dict(raw)
        for key, dependencies in edges.items():
            for dependency in dependencies:
                assert position[dependency] < position[key], (
                    f"{dependency!r} did not precede {key!r} in {order!r}"
                )

    @given(dags())
    @settings(max_examples=250, deadline=None)
    def test_levels_partition_every_key_exactly_once(self, raw: Raw) -> None:
        """The scheduler claims a whole wave, so a key in two waves runs twice."""
        flat = [key for level in _build(raw).levels() for key in level]
        assert sorted(flat) == sorted(key for key, _ in raw)
        assert len(flat) == len(set(flat))

    @given(dags())
    @settings(max_examples=250, deadline=None)
    def test_no_level_contains_its_own_dependency(self, raw: Raw) -> None:
        """If it did, a wave could start before the thing it waits on."""
        graph = _build(raw)
        edges = dict(raw)
        for level in graph.levels():
            level_set = set(level)
            for key in level:
                assert level_set.isdisjoint(edges[key]), (
                    f"{key!r} shares level {level!r} with its own dependency"
                )

    @given(dags())
    @settings(max_examples=250, deadline=None)
    def test_levels_and_topological_order_agree(self, raw: Raw) -> None:
        """Two orderings derived from the same graph must not disagree."""
        graph = _build(raw)
        assert graph.topological_order() == tuple(key for level in graph.levels() for key in level)

    @given(dags())
    @settings(max_examples=250, deadline=None)
    def test_every_dependency_sits_in_an_earlier_level(self, raw: Raw) -> None:
        """A dependency in the same or a later wave would be a scheduling race."""
        graph = _build(raw)
        edges = dict(raw)
        for level_index, level in enumerate(graph.levels()):
            earlier = {key for wave in graph.levels()[:level_index] for key in wave}
            for key in level:
                assert set(edges[key]) <= earlier

    @given(dags())
    @settings(max_examples=200, deadline=None)
    def test_ready_tasks_never_returns_a_key_with_unmet_dependencies(
        self, raw: list[tuple[str, tuple[str, ...]]]
    ) -> None:
        """The safety property the scheduler depends on.

        Walks the topological order completing one task at a time, and after
        each step checks that nothing offered as "ready" is waiting on
        something that has not finished.
        """
        graph = _build(raw)
        edges = dict(raw)
        completed: set[str] = set()
        for key in graph.topological_order():
            assert graph.is_ready(key, completed)
            for ready in graph.ready_tasks(completed):
                assert set(edges[ready]) <= completed
                assert ready not in completed
            completed.add(key)

    @given(dags())
    @settings(max_examples=200, deadline=None)
    def test_a_finished_task_is_never_offered_again(self, raw: Raw) -> None:
        """Regression: ``ready_tasks`` once returned completed tasks.

        A scheduler acting on that would execute a task twice, which is the
        duplicate-execution failure the whole design exists to prevent.
        """
        graph = _build(raw)
        completed: set[str] = set()
        for _ in range(len(graph)):
            for ready in graph.ready_tasks(completed):
                assert ready not in completed
            ready_now = graph.ready_tasks(completed)
            completed.update(ready_now)
        assert graph.ready_tasks(completed) == ()

    @given(dags())
    @settings(max_examples=200, deadline=None)
    def test_everything_is_schedulable(self, raw: Raw) -> None:
        """A valid DAG always drains: every task becomes ready at some point.

        Each round completes a whole wave, so the bound is ``len + 1`` rounds —
        one to start and one to confirm nothing is left. If it never drains, the
        ``for...else`` fires rather than looping forever.
        """
        graph = _build(raw)
        edges = dict(raw)
        completed: set[str] = set()
        for _ in range(len(graph) + 1):
            ready = graph.ready_tasks(completed)
            if not ready:
                break
            completed.update(ready)
        else:
            pytest.fail(f"ready_tasks never drained a valid DAG of {len(graph)} task(s)")
        assert completed == set(edges)


class TestReachabilityInvariants:
    """Closure queries must agree with the edges that were declared."""

    @given(dags())
    @settings(max_examples=200, deadline=None)
    def test_a_key_is_not_its_own_transitive_dependency(self, raw: Raw) -> None:
        """In an acyclic graph nothing can reach itself."""
        graph = _build(raw)
        for key, _ in raw:
            assert key not in graph.transitive_dependencies_of(key)
            assert key not in graph.transitive_dependents_of(key)

    @given(dags())
    @settings(max_examples=200, deadline=None)
    def test_direct_dependencies_are_always_transitive_ones(self, raw: Raw) -> None:
        """A one-hop closure must never be smaller than what it contains."""
        graph = _build(raw)
        for key, _ in raw:
            assert graph.dependencies_of(key) <= graph.transitive_dependencies_of(key)
            assert graph.dependents_of(key) <= graph.transitive_dependents_of(key)

    @given(dags())
    @settings(max_examples=200, deadline=None)
    def test_the_transitive_close_is_maximal(self, raw: Raw) -> None:
        """Every reachable node is reported, so nothing is silently dropped."""
        graph = _build(raw)
        edges = {key: set(deps) for key, deps in raw}

        def expected_closure(start: str) -> set[str]:
            seen: set[str] = set()
            frontier = list(edges[start])
            while frontier:
                current = frontier.pop()
                if current in seen:
                    continue
                seen.add(current)
                frontier.extend(edges[current])
            return seen

        for key in edges:
            assert graph.transitive_dependencies_of(key) == expected_closure(key)


class TestDeterminism:
    """The same definition always produces the same schedule."""

    @given(dags())
    @settings(max_examples=150, deadline=None)
    def test_ordering_does_not_depend_on_declaration_order(self, raw: Raw) -> None:
        """Two definitions with the same content in a different task order."""
        assert (
            _build(list(raw)).topological_order() == _build(list(reversed(raw))).topological_order()
        )
        assert _build(list(raw)).levels() == _build(list(reversed(raw))).levels()

    @given(dags())
    @settings(max_examples=150, deadline=None)
    def test_the_fingerprint_is_content_only(self, raw: Raw) -> None:
        """A definition's identity does not depend on how it was written."""
        first = WorkflowDefinition(
            name="n", version=1, tasks=tuple(TaskDefinition(k, "t", tuple(d)) for k, d in raw)
        )
        second = WorkflowDefinition(
            name="n",
            version=1,
            tasks=tuple(TaskDefinition(k, "t", tuple(reversed(d))) for k, d in raw),
        )
        assert first.fingerprint() == second.fingerprint()


class TestCycleDetectionIsSound:
    """Every graph either validates or is refused. There is no third outcome."""

    @given(arbitrary_graphs())
    @settings(max_examples=300, deadline=None)
    def test_a_graph_validates_or_reports_a_real_cycle(self, raw: Raw) -> None:
        """`CyclicDependencyError` must never be raised for an acyclic graph.

        A false positive is the dangerous failure: it refuses a valid workflow.
        So when validation fails on a cycle, every consecutive pair of the
        reported path — including the one that closes the loop — is checked
        against the declared edges.
        """
        edges = dict(raw)
        keys = set(edges)
        try:
            _build(raw)
        except UnknownDependencyError:
            # Not a cycle claim: a reference to a task that does not exist.
            return
        except CyclicDependencyError as error:
            cycle = error.cycle
            assert cycle, "a cycle was reported with no nodes"
            assert set(cycle) <= keys
            for index, node in enumerate(cycle):
                successor = cycle[(index + 1) % len(cycle)]
                assert successor in edges[node], (
                    f"reported edge {node!r} -> {successor!r} is not in the graph"
                )
        else:
            # Validated, so nothing may reach itself.
            for start, start_edges in edges.items():
                seen: set[str] = set()
                frontier = list(start_edges)
                while frontier:
                    current = frontier.pop()
                    if current == start:
                        pytest.fail(f"{start!r} was accepted despite reaching itself")
                    if current in seen or current not in edges:
                        continue
                    seen.add(current)
                    frontier.extend(edges[current])
