"""The OpenAPI document, and the documentation mirror that feeds it.

`api/serializers.py` says in its docstring that ``test_doc_serializer_matches_the_schema``
fails if the documentation mirror drifts from the validator. That claim is only
true if the test exists, so it does — and it is the reason the mirror can be a
plain serializer rather than a second validator.

The committed `docs/api/openapi.yaml` is checked for drift in CI, not here: a
test that regenerated it would be slow and would test the same thing twice.
"""

from __future__ import annotations

from api.serializers import TaskDocSerializer, WorkflowDefinitionDoc
from core.domain.schema import TASK_SCHEMA, WORKFLOW_DEFINITION_SCHEMA
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class TestDocSerializerMatchesSchema:
    """The mirror names the same fields, and requires the same ones."""

    def test_workflow_fields_match_the_schema(self) -> None:
        """A field added to one and not the other is a documentation defect."""
        assert set(WorkflowDefinitionDoc().fields) == set(WORKFLOW_DEFINITION_SCHEMA["properties"])

    def test_task_fields_match_the_schema(self) -> None:
        """Same rule, one level down."""
        assert set(TaskDocSerializer().fields) == set(TASK_SCHEMA["properties"])

    def test_required_workflow_fields_match_the_schema(self) -> None:
        """`required` is the part a client is most likely to get wrong."""
        required = {
            name for name, field in WorkflowDefinitionDoc().fields.items() if field.required
        }

        assert required == set(WORKFLOW_DEFINITION_SCHEMA["required"])

    def test_required_task_fields_match_the_schema(self) -> None:
        """`depends_on` and `config` are optional on both sides."""
        required = {name for name, field in TaskDocSerializer().fields.items() if field.required}

        assert required == set(TASK_SCHEMA["required"])


class TestSchemaEndpoint:
    """The schema is served for humans reading the docs site."""

    def test_the_schema_is_served(self, api_client: APIClient) -> None:
        """A broken generator must fail here rather than in a client."""
        response = api_client.get(reverse("schema"))

        assert response.status_code == status.HTTP_200_OK
        assert b"/api/v1/runs/" in response.content
        assert b"/api/v1/workflows/" in response.content
