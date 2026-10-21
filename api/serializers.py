"""Serializers.

Two rules shape this module.

**Output is derived from the database, not from the domain object.** A published
definition is rendered from its rows, so what a client sees is what was actually
stored. Rendering the in-memory `WorkflowDefinition` would show the caller what
they hoped they published.

**Input validation for definitions is *not* duplicated here.** The JSON Schema in
`core.domain.schema` is the single shape validator, and `parse_workflow_definition`
is the single parser; a DRF serializer repeating those rules would be a second
source of truth that drifts. `WorkflowDefinitionDoc` exists only so the OpenAPI
document can name the fields, and `test_doc_serializer_matches_the_schema` fails
if it stops matching the real schema.
"""

from __future__ import annotations

from typing import Any, ClassVar

from core.models import Task, TaskRun, Workflow, WorkflowRun
from rest_framework import serializers


class UserSerializer(serializers.Serializer[Any]):
    """The authenticated caller, as `/auth/me/` reports them."""

    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    is_staff = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


class TaskSerializer(serializers.ModelSerializer[Task]):
    """One task of a published definition."""

    class Meta:
        """Field mapping."""

        model = Task
        fields: ClassVar[list[str]] = ["key", "task_type", "depends_on", "config"]
        read_only_fields: ClassVar[list[str]] = fields


class WorkflowSerializer(serializers.ModelSerializer[Workflow]):
    """A published definition, with its tasks."""

    tasks = TaskSerializer(many=True, read_only=True)

    class Meta:
        """Field mapping."""

        model = Workflow
        fields: ClassVar[list[str]] = [
            "id",
            "name",
            "version",
            "description",
            "created_at",
            "tasks",
        ]
        read_only_fields: ClassVar[list[str]] = fields


class WorkflowSummarySerializer(serializers.ModelSerializer[Workflow]):
    """A definition in a list, without its tasks.

    The task list is what makes a definition large; a cursor-paginated list of
    20 definitions should not carry every task of every one of them.
    """

    task_count = serializers.IntegerField(read_only=True)

    class Meta:
        """Field mapping."""

        model = Workflow
        fields: ClassVar[list[str]] = [
            "id",
            "name",
            "version",
            "description",
            "created_at",
            "task_count",
        ]
        read_only_fields: ClassVar[list[str]] = fields


class TaskRunSerializer(serializers.ModelSerializer[TaskRun]):
    """One task's progress within a run."""

    task_key = serializers.CharField(source="task.key", read_only=True)

    class Meta:
        """Field mapping."""

        model = TaskRun
        fields: ClassVar[list[str]] = [
            "id",
            "task_key",
            "state",
            "attempt",
            "fencing_token",
            "lease_owner",
            "lease_expires_at",
            "created_at",
            "started_at",
            "finished_at",
        ]
        read_only_fields: ClassVar[list[str]] = fields


class WorkflowRunSerializer(serializers.ModelSerializer[WorkflowRun]):
    """A run, with the state of each of its tasks."""

    workflow_name = serializers.CharField(source="workflow.name", read_only=True)
    workflow_version = serializers.IntegerField(source="workflow.version", read_only=True)
    task_runs = TaskRunSerializer(many=True, read_only=True)

    class Meta:
        """Field mapping."""

        model = WorkflowRun
        fields: ClassVar[list[str]] = [
            "id",
            "workflow_name",
            "workflow_version",
            "state",
            "created_at",
            "started_at",
            "finished_at",
            "task_runs",
        ]
        read_only_fields: ClassVar[list[str]] = fields


class WorkflowRunSummarySerializer(serializers.ModelSerializer[WorkflowRun]):
    """A run in a list, without its task runs."""

    workflow_name = serializers.CharField(source="workflow.name", read_only=True)
    workflow_version = serializers.IntegerField(source="workflow.version", read_only=True)

    class Meta:
        """Field mapping."""

        model = WorkflowRun
        fields: ClassVar[list[str]] = [
            "id",
            "workflow_name",
            "workflow_version",
            "state",
            "created_at",
            "started_at",
            "finished_at",
        ]
        read_only_fields: ClassVar[list[str]] = fields


class TriggerSerializer(serializers.Serializer[Any]):
    """The body of a run trigger: which published definition to instantiate."""

    name = serializers.CharField(max_length=200)
    version = serializers.IntegerField(min_value=1)


class TaskDocSerializer(serializers.Serializer[Any]):
    """Documentation-only mirror of one task in `core.domain.schema`.

    Never validated. See the module docstring.
    """

    key = serializers.CharField()
    task_type = serializers.CharField()
    depends_on = serializers.ListField(child=serializers.CharField(), required=False)
    config = serializers.DictField(required=False)
    when = serializers.CharField(required=False, allow_null=True)
    join = serializers.ChoiceField(choices=["all", "any"], required=False)


class WorkflowDefinitionDoc(serializers.Serializer[Any]):
    """Documentation-only mirror of `WORKFLOW_DEFINITION_SCHEMA`.

    Exists so the OpenAPI document can name the fields instead of describing the
    body as a free-form object. `test_doc_serializer_matches_the_schema` asserts
    it stays in step with the real schema, which is the validator.
    """

    name = serializers.CharField()
    version = serializers.IntegerField()
    description = serializers.CharField(required=False)
    tasks = TaskDocSerializer(many=True)
