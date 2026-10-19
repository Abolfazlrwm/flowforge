"""Tests for publishing and loading definitions against the database."""

from __future__ import annotations

import pytest
from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.errors import DefinitionError, UnknownDependencyError
from core.models import Task, Workflow
from core.repositories.definitions import (
    DefinitionConflictError,
    definition_exists,
    load_definition,
    publish_definition,
)

# Every test here touches the database. The marker — rather than a `db` fixture
# parameter on each test — keeps the signatures honest: an argument that is
# never read, only there to switch database access on, is a lie about what the
# test needs. Matches `tests/unit/test_constraints.py`.
pytestmark = pytest.mark.django_db


def _definition(
    *,
    name: str = "etl",
    version: int = 1,
    tasks: tuple[TaskDefinition, ...] = (
        TaskDefinition("extract", "http"),
        TaskDefinition("load", "sql", depends_on=("extract",), config={"dsn": "x"}),
    ),
) -> WorkflowDefinition:
    return WorkflowDefinition(name=name, version=version, tasks=tasks)


def _workflow_row_count() -> int:
    return Workflow.objects.count()


class TestPublish:
    """Publishing writes the definition, exactly once."""

    def test_publish_creates_the_workflow_and_its_tasks(self) -> None:
        """One workflow row plus one row per task, all keyed by the declared key."""
        workflow = publish_definition(_definition())
        assert workflow.name == "etl"
        assert workflow.version == 1
        assert set(Task.objects.filter(workflow=workflow).values_list("key", flat=True)) == {
            "extract",
            "load",
        }

    def test_publish_returns_the_same_row_for_the_same_content(self) -> None:
        """Idempotent, so a retry after a timeout is not an error."""
        first = publish_definition(_definition())
        second = publish_definition(_definition())
        assert first.id == second.id
        assert _workflow_row_count() == 1

    def test_publish_refuses_different_content_under_the_same_version(self) -> None:
        """A run must trace back to exactly the definition it executed.

        Without this, "etl v3" would mean whatever was posted last, and a run
        six months old could no longer be explained.
        """
        publish_definition(_definition())
        changed = WorkflowDefinition(
            name="etl",
            version=1,
            tasks=(TaskDefinition("extract", "http"),),
        )
        with pytest.raises(DefinitionConflictError, match="already exists with different content"):
            publish_definition(changed)

    def test_a_bumped_version_is_a_separate_definition(self) -> None:
        """Versions are additive: v2 does not replace v1, it sits beside it."""
        publish_definition(_definition(version=1))
        publish_definition(_definition(version=2))
        assert Workflow.objects.count() == 2

    def test_publish_stores_every_field_of_every_task(self) -> None:
        """A lossy write here would silently change what a run was based on."""
        workflow = publish_definition(_definition())
        load = Task.objects.get(workflow=workflow, key="load")
        assert load.task_type == "sql"
        assert load.depends_on == ["extract"]
        assert load.config == {"dsn": "x"}

    def test_publish_refuses_a_cyclic_definition(self) -> None:
        """Refused before anything is written, not rolled back afterwards."""
        cyclic = WorkflowDefinition(
            name="c",
            version=1,
            tasks=(
                TaskDefinition("a", "t", depends_on=("b",)),
                TaskDefinition("b", "t", depends_on=("a",)),
            ),
        )
        with pytest.raises(DefinitionError, match="cycle"):
            publish_definition(cyclic)
        assert _workflow_row_count() == 0

    def test_a_failed_publish_leaves_no_partial_rows(self) -> None:
        """Atomic: the workflow and its tasks appear together or not at all.

        A workflow with half its tasks would schedule the missing ones forever.
        """
        with pytest.raises(DefinitionConflictError):
            publish_definition(_definition())
            publish_definition(
                WorkflowDefinition(name="etl", version=1, tasks=(TaskDefinition("other", "t"),))
            )
        reloaded = load_definition(Workflow.objects.get(name="etl", version=1))
        assert reloaded.task_keys == ("extract", "load")

    def test_definition_exists_reports_publication(self) -> None:
        """The cheap question a publish endpoint should ask before doing work."""
        assert definition_exists(name="etl", version=1) is False
        publish_definition(_definition())
        assert definition_exists(name="etl", version=1) is True
        assert definition_exists(name="etl", version=2) is False


class TestLoad:
    """Loading reconstructs exactly what was published."""

    def test_round_trip(self) -> None:
        """Value-object equality, so a load is indistinguishable from the original."""
        workflow = publish_definition(_definition())
        reloaded = load_definition(workflow)
        assert reloaded == _definition()

    def test_the_fingerprint_survives_a_round_trip(self) -> None:
        """This is what makes republishing idempotent."""
        workflow = publish_definition(_definition())
        assert load_definition(workflow).fingerprint() == _definition().fingerprint()

    def test_task_order_is_not_required_to_match(self) -> None:
        """Rows come back by insertion order, and the graph re-validates.

        Stored order is an artefact of how the rows were written, so a load that
        depended on it would be a definition that changes when the storage does.
        """
        workflow = publish_definition(_definition())
        reloaded = load_definition(workflow)
        assert reloaded.graph.topological_order() == ("extract", "load")

    def test_an_empty_definition_round_trips(self) -> None:
        """A schema-valid but useless workflow: it must still load, not crash."""
        workflow = publish_definition(WorkflowDefinition(name="empty", version=1))
        assert load_definition(workflow).task_keys == ()

    def test_load_rebuilds_the_graph(self) -> None:
        """The graph is rebuilt from stored edges, not read back as a blob."""
        workflow = publish_definition(_definition())
        assert load_definition(workflow).graph.dependencies_of("load") == {"extract"}

    def test_a_row_manually_broken_is_refused_on_load(self) -> None:
        """The safety net.

        ``load`` now depends on a task that no longer exists. Reconstructing it
        silently would let the scheduler spin on a task that can never start.
        """
        workflow = publish_definition(_definition())
        Task.objects.filter(workflow=workflow, key="load").update(depends_on=["ghost"])
        with pytest.raises(UnknownDependencyError, match="unknown task 'ghost'"):
            load_definition(Workflow.objects.get(id=workflow.id))

    def test_a_row_with_no_task_type_is_refused_on_load(self) -> None:
        """Only possible in a database that predates migration ``core.0002``."""
        workflow = publish_definition(_definition())
        Task.objects.filter(workflow=workflow, key="extract").update(task_type="")
        with pytest.raises(DefinitionError, match=r"core\.0002"):
            load_definition(Workflow.objects.get(id=workflow.id))

    def test_loading_a_definition_does_not_mutate_the_rows(self) -> None:
        """Loading is read-only: a second load must not duplicate or rewrite tasks."""
        workflow = publish_definition(_definition())
        load_definition(workflow)
        load_definition(workflow)
        assert Task.objects.filter(workflow=workflow).count() == 2

    def test_two_versions_load_independently(self) -> None:
        """Same name, different content: neither load may see the other's tasks."""
        publish_definition(_definition(version=1))
        publish_definition(
            _definition(
                version=2,
                tasks=(
                    TaskDefinition("extract", "http"),
                    TaskDefinition("transform", "sql", depends_on=("extract",)),
                    TaskDefinition("load", "sql", depends_on=("transform",)),
                ),
            )
        )
        v1 = load_definition(Workflow.objects.get(name="etl", version=1))
        v2 = load_definition(Workflow.objects.get(name="etl", version=2))
        assert v1.task_keys == ("extract", "load")
        assert v2.graph.topological_order() == ("extract", "transform", "load")
