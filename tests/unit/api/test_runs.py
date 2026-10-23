"""Triggering, listing and reading runs.

Three things are asserted here that the service tests do not cover:

* the trigger's initial states survive the round trip to JSON — a task with no
  dependencies is ``ready`` and one with dependencies is ``pending``;
* an ``Idempotency-Key`` replays the original run instead of creating a second
  one, and a key reused with a different body is a 422 rather than a replay;
* runs are per **organization**. Every member of the owning tenant sees them,
  another tenant sees nothing, and reading another tenant's run by id is a 404
  rather than a 403 — the row is invisible, not merely forbidden.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.domain.schema import parse_workflow_definition
from core.models import TaskRun, WorkflowRun
from core.repositories.definitions import publish_definition
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tests.unit.api.payloads import definition_payload

LIST_URL = "run-list"


def _trigger(client: APIClient, **extra: Any) -> Any:
    """Trigger ``etl`` v1, with any extra headers passed through."""
    body = {"name": "etl", "version": 1}
    return client.post(reverse(LIST_URL), body, format="json", **extra)


def _detail_url(run_id: Any) -> str:
    """The canonical URL of a run."""
    return reverse("run-detail", kwargs={"pk": run_id})


class TestTrigger:
    """A trigger creates one run and one task run per task."""

    def test_triggering_returns_202_with_the_initial_states(
        self, user_client: APIClient, published: Any
    ) -> None:
        """202, not 201: the work is accepted, not finished."""
        response = _trigger(user_client)

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["state"] == "pending"
        states = {
            task_run["task_key"]: task_run["state"] for task_run in response.data["task_runs"]
        }
        assert states == {"extract": "ready", "load": "pending"}
        assert WorkflowRun.objects.count() == 1
        assert TaskRun.objects.count() == 2

    def test_the_run_records_who_asked(
        self, user_client: APIClient, user: Any, published: Any
    ) -> None:
        """Ownership is what the visibility rule is built on."""
        _trigger(user_client)

        assert WorkflowRun.objects.get().requested_by_id == user.pk

    def test_an_unknown_definition_is_404(self, user_client: APIClient, published: Any) -> None:
        """A version that was never published is not a run."""
        response = user_client.post(reverse(LIST_URL), {"name": "etl", "version": 2}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "not_found"
        assert WorkflowRun.objects.count() == 0

    def test_a_malformed_trigger_is_400_with_field_details(
        self, user_client: APIClient, published: Any
    ) -> None:
        """The missing field is named, not just "invalid"."""
        response = user_client.post(reverse(LIST_URL), {"name": "etl"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "validation_error"
        assert "version" in response.data["error"]["details"]

    def test_an_unauthenticated_caller_cannot_trigger(
        self, api_client: APIClient, published: Any
    ) -> None:
        """A run needs an owner; anonymous triggers would have none."""
        response = api_client.post(reverse(LIST_URL), {"name": "etl", "version": 1}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestIdempotency:
    """The header is optional; when present, it is a promise."""

    def test_a_replay_returns_the_same_run(self, user_client: APIClient, published: Any) -> None:
        """A retried request must not start the work twice."""
        first = _trigger(user_client, HTTP_IDEMPOTENCY_KEY="k-1")
        second = _trigger(user_client, HTTP_IDEMPOTENCY_KEY="k-1")

        assert first.status_code == status.HTTP_202_ACCEPTED
        assert second.status_code == status.HTTP_202_ACCEPTED
        assert first.data["id"] == second.data["id"]
        assert "Idempotent-Replay" not in first
        assert second["Idempotent-Replay"] == "true"
        assert WorkflowRun.objects.count() == 1

    def test_a_key_reused_with_a_different_body_is_422(
        self, user_client: APIClient, published: Any, organization: Any
    ) -> None:
        """Replaying the original would silently discard this request."""
        publish_definition(
            parse_workflow_definition(definition_payload(name="other")),
            organization=organization,
        )

        first = _trigger(user_client, HTTP_IDEMPOTENCY_KEY="k-2")
        second = user_client.post(
            reverse(LIST_URL),
            {"name": "other", "version": 1},
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-2",
        )

        assert first.status_code == status.HTTP_202_ACCEPTED
        assert second.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert second.data["error"]["code"] == "idempotency_mismatch"
        assert WorkflowRun.objects.count() == 1

    def test_without_a_key_a_retry_creates_a_second_run(
        self, user_client: APIClient, published: Any
    ) -> None:
        """The header is opt-in, and its absence is not an error."""
        _trigger(user_client)
        _trigger(user_client)

        assert WorkflowRun.objects.count() == 2

    def test_the_key_is_scoped_to_the_user(
        self, user_client: APIClient, other_client: APIClient, published: Any
    ) -> None:
        """One user's key must not suppress another user's request."""
        first = _trigger(user_client, HTTP_IDEMPOTENCY_KEY="shared")
        second = _trigger(other_client, HTTP_IDEMPOTENCY_KEY="shared")

        assert first.data["id"] != second.data["id"]
        assert WorkflowRun.objects.count() == 2


class TestRunVisibility:
    """Runs are per organization; staff are the operational exception."""

    def test_every_member_of_the_tenant_sees_the_tenants_runs(
        self, user_client: APIClient, other_client: APIClient, published: Any
    ) -> None:
        """Visibility is a tenant boundary, not a per-user one."""
        _trigger(user_client)

        assert len(other_client.get(reverse(LIST_URL)).data["results"]) == 1
        assert len(user_client.get(reverse(LIST_URL)).data["results"]) == 1

    def test_another_tenant_sees_nothing(
        self, user_client: APIClient, stranger_client: APIClient, published: Any
    ) -> None:
        """A member of another organization must not learn what this one ran."""
        _trigger(user_client)

        assert stranger_client.get(reverse(LIST_URL)).data["results"] == []

    def test_staff_see_every_run(
        self, user_client: APIClient, staff_client: APIClient, published: Any
    ) -> None:
        """Operators need the whole picture, across tenants."""
        _trigger(user_client)

        assert len(staff_client.get(reverse(LIST_URL)).data["results"]) == 1

    def test_a_member_can_read_a_run_in_their_tenant(
        self, user_client: APIClient, other_client: APIClient, published: Any
    ) -> None:
        """The detail carries the task-level progress, not just the run state."""
        created = _trigger(user_client)

        response = other_client.get(_detail_url(created.data["id"]))

        assert response.status_code == status.HTTP_200_OK
        assert {task_run["task_key"] for task_run in response.data["task_runs"]} == {
            "extract",
            "load",
        }

    def test_reading_another_tenants_run_is_404(
        self, user_client: APIClient, stranger_client: APIClient, published: Any
    ) -> None:
        """404, not 403: the row is invisible, so its existence does not leak."""
        created = _trigger(user_client)

        response = stranger_client.get(_detail_url(created.data["id"]))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "not_found"

    def test_staff_can_read_anyones_run(
        self, user_client: APIClient, staff_client: APIClient, published: Any
    ) -> None:
        """The staff bypass works across tenants, and it is deliberate."""
        created = _trigger(user_client)

        assert staff_client.get(_detail_url(created.data["id"])).status_code == status.HTTP_200_OK

    def test_an_unknown_run_is_404(self, user_client: APIClient) -> None:
        """A well-formed but unused UUID is simply not found."""
        response = user_client.get(_detail_url(uuid.uuid4()))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"]["code"] == "not_found"


class TestRunFilters:
    """The filter set is the declared, testable list of allowed parameters."""

    def test_filtering_by_state(self, user_client: APIClient, published: Any) -> None:
        """A trigger leaves the run ``pending``."""
        _trigger(user_client)

        assert len(user_client.get(reverse(LIST_URL), {"state": "pending"}).data["results"]) == 1
        assert user_client.get(reverse(LIST_URL), {"state": "running"}).data["results"] == []

    def test_filtering_by_definition_name(self, user_client: APIClient, published: Any) -> None:
        """Filtering by workflow name, not by its UUID, because that is what a caller knows."""
        _trigger(user_client)

        matching = user_client.get(reverse(LIST_URL), {"workflow": "etl"})
        other = user_client.get(reverse(LIST_URL), {"workflow": "nope"})

        assert len(matching.data["results"]) == 1
        assert other.data["results"] == []
