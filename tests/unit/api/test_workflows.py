"""Publishing, listing and reading workflow definitions.

The behaviour under test is the HTTP contract: status codes, the error envelope,
which fields are exposed, and who is allowed to write. The domain rules behind a
rejection (cycles, unknown dependencies) already have their own tests; what is
asserted here is that a rejection *reaches the caller* as a 400 with a usable
message instead of a 500.
"""

from __future__ import annotations

from typing import Any

from core.models import Task, Workflow
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tests.unit.api.payloads import definition_payload

LIST_URL = "workflow-list"


def _detail_url(name: str, version: int) -> str:
    """The canonical URL of a definition."""
    return reverse("workflow-detail", kwargs={"name": name, "version": version})


class TestPublish:
    """Publishing is staff-only, idempotent, and exclusive by version."""

    def test_publishing_returns_201_and_the_stored_rows(self, staff_client: APIClient) -> None:
        """201, and what the response shows is what was persisted."""
        response = staff_client.post(reverse(LIST_URL), definition_payload(), format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == "etl"
        assert response.data["version"] == 1
        assert {task["key"] for task in response.data["tasks"]} == {"extract", "load"}
        assert Workflow.objects.filter(name="etl", version=1).exists()
        assert Task.objects.filter(workflow__name="etl").count() == 2

    def test_republishing_identical_content_is_200_not_201(self, staff_client: APIClient) -> None:
        """A retry after a timeout must not look like a second publish."""
        url = reverse(LIST_URL)
        first = staff_client.post(url, definition_payload(), format="json")
        second = staff_client.post(url, definition_payload(), format="json")

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_200_OK
        assert first.data["id"] == second.data["id"]
        assert Workflow.objects.count() == 1

    def test_different_content_under_the_same_version_is_409(self, staff_client: APIClient) -> None:
        """Silently overwriting would change what past runs executed."""
        url = reverse(LIST_URL)
        staff_client.post(url, definition_payload(), format="json")

        response = staff_client.post(url, definition_payload(description="changed"), format="json")

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.data["error"]["code"] == "conflict"
        assert Workflow.objects.count() == 1

    def test_a_non_staff_caller_cannot_publish(self, user_client: APIClient) -> None:
        """Publishing changes what every future run executes."""
        response = user_client.post(reverse(LIST_URL), definition_payload(), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error"]["code"] == "permission_denied"
        assert Workflow.objects.count() == 0

    def test_an_unauthenticated_caller_cannot_publish(self, api_client: APIClient) -> None:
        """No credentials is 401, not 403: there is nobody to be forbidden."""
        response = api_client.post(reverse(LIST_URL), definition_payload(), format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data["error"]["code"] == "authentication_required"

    def test_a_non_staff_caller_can_read(self, user_client: APIClient) -> None:
        """Reading is not privileged."""
        assert user_client.get(reverse(LIST_URL)).status_code == status.HTTP_200_OK


class TestPublishValidation:
    """Every rejection is a 400 whose message names the problem."""

    def _publish(self, client: APIClient, payload: Any) -> Any:
        """Post ``payload`` and return the response."""
        return client.post(reverse(LIST_URL), payload, format="json")

    def test_a_missing_field_is_400_and_names_the_path(self, staff_client: APIClient) -> None:
        """The JSONPath is the difference between "bad" and "fix this"."""
        payload = definition_payload()
        del payload["tasks"]

        response = self._publish(staff_client, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "validation_error"
        assert "$" in response.data["error"]["message"]
        assert "tasks" in response.data["error"]["message"]
        assert Workflow.objects.count() == 0

    def test_an_unknown_field_is_400(self, staff_client: APIClient) -> None:
        """`additionalProperties: false` is what makes a typo loud."""
        payload = definition_payload()
        payload["unexpected"] = True

        assert self._publish(staff_client, payload).status_code == status.HTTP_400_BAD_REQUEST

    def test_an_unknown_dependency_is_400(self, staff_client: APIClient) -> None:
        """A well-formed payload can still be an unschedulable graph."""
        payload = definition_payload(
            tasks=[{"key": "load", "task_type": "load", "depends_on": ["ghost"]}]
        )

        response = self._publish(staff_client, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "ghost" in response.data["error"]["message"]
        assert Workflow.objects.count() == 0

    def test_a_cycle_is_400(self, staff_client: APIClient) -> None:
        """A cycle can never be scheduled, so it must never be stored."""
        payload = definition_payload(
            tasks=[
                {"key": "a", "task_type": "t", "depends_on": ["b"]},
                {"key": "b", "task_type": "t", "depends_on": ["a"]},
            ]
        )

        response = self._publish(staff_client, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cycle" in response.data["error"]["message"].lower()
        assert Workflow.objects.count() == 0

    def test_a_duplicate_task_key_is_400(self, staff_client: APIClient) -> None:
        """JSON Schema cannot compare across objects, so this is a domain error."""
        payload = definition_payload(
            tasks=[
                {"key": "a", "task_type": "t"},
                {"key": "a", "task_type": "t"},
            ]
        )

        response = self._publish(staff_client, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "duplicate" in response.data["error"]["message"].lower()
        assert Workflow.objects.count() == 0


class TestList:
    """The list is a summary: it must not carry every task of every definition."""

    def test_the_list_returns_summaries_with_a_task_count(self, staff_client: APIClient) -> None:
        """`tasks` is the field that makes a list expensive, so it is absent."""
        staff_client.post(reverse(LIST_URL), definition_payload(), format="json")

        result = staff_client.get(reverse(LIST_URL)).data["results"][0]

        assert result["task_count"] == 2
        assert "tasks" not in result

    def test_the_list_is_newest_first(self, staff_client: APIClient) -> None:
        """A stable order is what makes the cursor meaningful."""
        url = reverse(LIST_URL)
        staff_client.post(url, definition_payload(name="first"), format="json")
        staff_client.post(url, definition_payload(name="second"), format="json")

        results = staff_client.get(url).data["results"]

        timestamps = [item["created_at"] for item in results]
        assert timestamps == sorted(timestamps, reverse=True)
        assert {item["name"] for item in results} == {"first", "second"}

    def test_filtering_by_exact_name(self, staff_client: APIClient) -> None:
        """`?name=` is exact, so a prefix does not leak in."""
        url = reverse(LIST_URL)
        staff_client.post(url, definition_payload(name="etl"), format="json")
        staff_client.post(url, definition_payload(name="etl-daily"), format="json")

        results = staff_client.get(url, {"name": "etl"}).data["results"]

        assert [item["name"] for item in results] == ["etl"]

    def test_filtering_by_name_prefix(self, staff_client: APIClient) -> None:
        """`?name__startswith=` exists so a client does not fetch everything."""
        url = reverse(LIST_URL)
        for name in ("etl", "etl-daily", "other"):
            staff_client.post(url, definition_payload(name=name), format="json")

        results = staff_client.get(url, {"name__startswith": "etl"}).data["results"]

        assert {item["name"] for item in results} == {"etl", "etl-daily"}

    def test_filtering_by_version(self, staff_client: APIClient) -> None:
        """Versions are part of the identity, so they are filterable."""
        url = reverse(LIST_URL)
        staff_client.post(url, definition_payload(version=1), format="json")
        staff_client.post(url, definition_payload(version=2), format="json")

        results = staff_client.get(url, {"version": 2}).data["results"]

        assert [item["version"] for item in results] == [2]


class TestDetail:
    """A definition is addressed by `(name, version)`, not by its UUID."""

    def test_the_detail_returns_the_definition_with_its_tasks(
        self, staff_client: APIClient
    ) -> None:
        """The detail is the full document, tasks included."""
        staff_client.post(reverse(LIST_URL), definition_payload(), format="json")

        response = staff_client.get(_detail_url("etl", 1))

        assert response.status_code == status.HTTP_200_OK
        assert {task["key"] for task in response.data["tasks"]} == {"extract", "load"}
        load = next(task for task in response.data["tasks"] if task["key"] == "load")
        assert load["depends_on"] == ["extract"]

    def test_the_url_is_name_and_version_not_the_uuid(self, staff_client: APIClient) -> None:
        """The caller already knows both values; a UUID lookup is a second trip."""
        created = staff_client.post(reverse(LIST_URL), definition_payload(), format="json")

        fetched = staff_client.get(_detail_url("etl", 1))

        assert str(fetched.data["id"]) == str(created.data["id"])

    def test_an_unknown_version_is_404(self, staff_client: APIClient) -> None:
        """A different version is a different definition, not a redirect."""
        staff_client.post(reverse(LIST_URL), definition_payload(), format="json")

        response = staff_client.get(_detail_url("etl", 99))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "not_found"


class TestPagination:
    """The cursor must not duplicate or skip rows."""

    def test_the_cursor_walks_every_row_exactly_once(self, staff_client: APIClient) -> None:
        """Two pages, no overlap, no gap."""
        url = reverse(LIST_URL)
        for index in range(3):
            staff_client.post(url, definition_payload(name=f"wf-{index}"), format="json")

        first = staff_client.get(url, {"page_size": 2})
        second = staff_client.get(first.data["next"])

        seen = [item["id"] for item in first.data["results"]]
        seen += [item["id"] for item in second.data["results"]]
        assert len(first.data["results"]) == 2
        assert len(second.data["results"]) == 1
        assert len(set(seen)) == 3
