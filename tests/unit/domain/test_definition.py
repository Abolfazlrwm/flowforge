"""Tests for TaskDefinition and WorkflowDefinition as value objects."""

from __future__ import annotations

import pytest
from core.domain.definition import (
    DependencyGraph,
    TaskDefinition,
    WorkflowDefinition,
)
from core.domain.errors import (
    CyclicDependencyError,
    DefinitionError,
    UnknownDependencyError,
)


class TestTaskDefinition:
    """The invariants a single task carries by itself."""

    def test_a_minimal_task_is_valid(self) -> None:
        """A task needs a key and a task_type; everything else defaults."""
        task = TaskDefinition(key="extract", task_type="http")
        assert task.depends_on == ()
        assert dict(task.config) == {}

    def test_key_must_not_be_blank(self) -> None:
        """A blank key could never be referenced by ``depends_on``."""
        with pytest.raises(DefinitionError, match="task key must not be blank"):
            TaskDefinition(key="   ", task_type="http")

    def test_task_type_is_required(self) -> None:
        """Without one, no handler could be selected at run time."""
        with pytest.raises(DefinitionError, match="task 'a' has no task_type"):
            TaskDefinition(key="a", task_type="")

    def test_a_blank_dependency_is_rejected(self) -> None:
        """An empty entry is a copy-paste slip, not a dependency."""
        with pytest.raises(DefinitionError, match="blank dependency"):
            TaskDefinition(key="a", task_type="http", depends_on=("exists", "  "))

    def test_the_same_dependency_listed_twice_is_rejected(self) -> None:
        """A duplicate is noise, not a stronger dependency.

        JSON Schema also rejects duplicate ``depends_on`` entries; this guards
        the Python construction path, which the schema never sees.
        """
        with pytest.raises(DefinitionError, match="dependency 'b' twice"):
            TaskDefinition(key="a", task_type="http", depends_on=("b", "b"))

    def test_depends_on_order_does_not_change_the_graph(self) -> None:
        """Order carries no meaning, so it cannot change the schedule.

        ``topological_order`` and ``levels`` break ties by key. If the raw order
        survived into the graph, two payloads differing only in the order of one
        list would schedule differently.
        """
        graph = DependencyGraph(
            [
                TaskDefinition(key="z", task_type="t"),
                TaskDefinition(key="m", task_type="t"),
                TaskDefinition(key="b", task_type="t"),
                TaskDefinition(key="a", task_type="t", depends_on=("z", "m", "b")),
            ]
        )
        assert graph.dependencies_of("a") == frozenset({"z", "m", "b"})
        assert graph.topological_order() == ("b", "m", "z", "a")

    def test_config_cannot_be_mutated_after_construction(self) -> None:
        """The caller hands us their dict; we must not keep a live reference.

        A definition is validated once. If a later mutation to the original
        mapping changed the configuration after validation, the definition a run
        executed would not be the definition that was checked.
        """
        original = {"retries": 3}
        task = TaskDefinition(key="a", task_type="t", config=original)
        original["retries"] = 99
        assert dict(task.config) == {"retries": 3}

    def test_nested_config_survives_construction(self) -> None:
        """Freezing is shallow; nested values stay usable as written."""
        task = TaskDefinition(key="a", task_type="t", config={"nested": {"x": 1}})
        assert dict(task.config) == {"nested": {"x": 1}}

    def test_equal_tasks_compare_equal(self) -> None:
        """Value semantics, so two identical tasks are the same definition."""
        assert TaskDefinition(key="a", task_type="t") == TaskDefinition(key="a", task_type="t")

    def test_tasks_differing_only_in_config_are_not_equal(self) -> None:
        """Config is part of identity.

        Calling them equal would let a republished change through as a no-op.
        """
        left = TaskDefinition(key="a", task_type="t", config={"x": 1})
        right = TaskDefinition(key="a", task_type="t", config={"x": 2})
        assert left != right
        assert hash(left) != hash(right)

    def test_a_task_is_hashable_even_with_structured_config(self) -> None:
        """Definitions are used as dict keys for deduplication."""
        config = {"schedule": {"cron": "* * * * *"}, "retries": [1, 2]}
        task = TaskDefinition(key="a", task_type="t", config=config)
        assert hash(task) == hash(TaskDefinition(key="a", task_type="t", config=config))

    def test_a_config_value_that_is_not_json_is_refused(self) -> None:
        """Refused at construction, not at the JSON dump that would fail later.

        ``config`` becomes a ``JSONField``, so the whole definition is only
        storable if every value is JSON-shaped. Letting a ``datetime`` through
        construction would move the failure to persistence, or to a worker
        deserialising it on another machine.
        """
        with pytest.raises(TypeError, match="not valid JSON configuration"):
            TaskDefinition(key="a", task_type="t", config={"when": object()})


class TestWorkflowDefinition:
    """The invariants of the definition as a whole."""

    def test_a_single_task_definition_is_valid(self) -> None:
        """The graph is built from the definition's own tasks."""
        definition = WorkflowDefinition(name="noop", version=1, tasks=(TaskDefinition("a", "t"),))
        assert definition.graph.topological_order() == ("a",)

    def test_a_definition_with_no_tasks_is_valid(self) -> None:
        """An empty definition is syntactically fine.

        Nothing schedules, so a run over it would sit in PENDING. Rejecting it
        here would be a policy decision that belongs at the point of use.
        """
        definition = WorkflowDefinition(name="empty", version=1)
        assert definition.tasks == ()

    def test_name_must_not_be_blank(self) -> None:
        """A definition with no name could not be referenced to start a run."""
        with pytest.raises(DefinitionError, match="workflow name must not be blank"):
            WorkflowDefinition(name=" ", version=1)

    def test_version_must_be_positive(self) -> None:
        """``version`` is part of the identity ``(name, version)``."""
        with pytest.raises(DefinitionError, match="version must be >= 1"):
            WorkflowDefinition(name="v", version=0)

    def test_task_keys_are_the_keys_in_declaration_order(self) -> None:
        """Declaration order, not sorted order — that is the graph's job."""
        definition = WorkflowDefinition(
            name="n",
            version=1,
            tasks=(TaskDefinition("c", "t"), TaskDefinition("a", "t")),
        )
        assert definition.task_keys == ("c", "a")

    def test_task_lookup_by_key(self) -> None:
        """A run refers to a step by name, so lookup must work by name."""
        definition = WorkflowDefinition(name="n", version=1, tasks=(TaskDefinition("a", "t"),))
        assert definition.task("a").task_type == "t"

    def test_task_lookup_of_an_unknown_key_raises(self) -> None:
        """The error names the key rather than raising a bare ``KeyError``."""
        definition = WorkflowDefinition(name="n", version=1, tasks=(TaskDefinition("a", "t"),))
        with pytest.raises(UnknownDependencyError):
            definition.task("nope")

    def test_graph_is_validated_when_built(self) -> None:
        """The graph caches the validation, so it is refused on first access."""
        definition = WorkflowDefinition(
            name="n",
            version=1,
            tasks=(
                TaskDefinition("a", "t", depends_on=("b",)),
                TaskDefinition("b", "t", depends_on=("a",)),
            ),
        )
        with pytest.raises(CyclicDependencyError):
            _ = definition.graph

    def test_name_and_version_identify_a_definition(self) -> None:
        """Two definitions that differ only in version are different."""
        left = WorkflowDefinition(name="n", version=1, tasks=(TaskDefinition("a", "t"),))
        right = WorkflowDefinition(name="n", version=2, tasks=(TaskDefinition("a", "t"),))
        assert left != right


class TestFingerprint:
    """The content fingerprint used to make publishing idempotent."""

    def _definition(self) -> WorkflowDefinition:
        """A fixed two-task definition, mutated by each test."""
        return WorkflowDefinition(
            name="etl",
            version=1,
            tasks=(
                TaskDefinition("extract", "http"),
                TaskDefinition("load", "sql", depends_on=("extract",), config={"dsn": "x"}),
            ),
        )

    def test_fingerprint_is_stable(self) -> None:
        """The same content always digests to the same value."""
        assert self._definition().fingerprint() == self._definition().fingerprint()

    def test_dependency_order_does_not_change_the_fingerprint(self) -> None:
        """A definition is the same definition when a list is reordered."""
        left = WorkflowDefinition(
            name="n", version=1, tasks=(TaskDefinition("a", "t", depends_on=("x", "y")),)
        )
        right = WorkflowDefinition(
            name="n", version=1, tasks=(TaskDefinition("a", "t", depends_on=("y", "x")),)
        )
        assert left.fingerprint() == right.fingerprint()

    def test_a_changed_task_type_changes_the_fingerprint(self) -> None:
        """Swapping the handler is a content change, not a repost."""
        left = self._definition()
        right = WorkflowDefinition(
            name="etl",
            version=1,
            tasks=(
                TaskDefinition("extract", "http"),
                TaskDefinition("load", "postgres", depends_on=("extract",), config={"dsn": "x"}),
            ),
        )
        assert left.fingerprint() != right.fingerprint()

    def test_a_changed_config_changes_the_fingerprint(self) -> None:
        """A changed parameter must not be mistaken for an identical repost."""
        left = self._definition()
        right = WorkflowDefinition(
            name="etl",
            version=1,
            tasks=(
                TaskDefinition("extract", "http"),
                TaskDefinition("load", "sql", depends_on=("extract",), config={"dsn": "y"}),
            ),
        )
        assert left.fingerprint() != right.fingerprint()

    def test_a_different_version_changes_the_fingerprint(self) -> None:
        """``version`` is part of the content, as it is part of the identity."""
        left = self._definition()
        right = WorkflowDefinition(name="etl", version=2, tasks=left.tasks)
        assert left.fingerprint() != right.fingerprint()

    def test_a_changed_description_changes_the_fingerprint(self) -> None:
        """Description is content, so it must not be silently overwritten.

        Reposting a definition whose only change is the description used to
        return the stored row unchanged, leaving the caller believing it had
        published text the database did not hold.
        """
        left = self._definition()
        right = WorkflowDefinition(
            name=left.name,
            version=left.version,
            tasks=left.tasks,
            description="nightly instead of hourly",
        )
        assert left.description != right.description
        assert left.fingerprint() != right.fingerprint()
