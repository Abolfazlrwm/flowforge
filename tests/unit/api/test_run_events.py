"""Reading a run's event log.

The endpoint is read-only history: it returns the task-run events the scheduler
and workers recorded, oldest first, and it enforces the same per-user visibility
as the run detail — another user's run is a 404, not a 403.
"""

from __future__ import annotations

from typing import Any

from core.domain.states import TaskRunState
from core.repositories.transitions import claim_task_run, finish_task_run
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tests.conftest import NOW


def _trigger(client: APIClient) -> Any:
    """Trigger ``etl`` v1 and return the response."""
    return client.post(reverse("run-list"), {"name": "etl", "version": 1}, format="json")


def _events_url(run_id: Any) -> str:
    """The canonical URL of a run's event log."""
    return reverse("run-events", kwargs={"pk": run_id})


def _extract_task_run_id(payload: Any) -> Any:
    """The id of the dependency-free ``extract`` task run in a trigger payload."""
    return next(tr["id"] for tr in payload["task_runs"] if tr["task_key"] == "extract")


class TestRunEvents:
    """``GET /runs/<id>/events/`` returns the run's task-run event log."""

    def test_a_freshly_triggered_run_has_an_empty_log(
        self, user_client: APIClient, published: Any
    ) -> None:
        """Materialising a run creates no events; only transitions do."""
        created = _trigger(user_client)

        response = user_client.get(_events_url(created.data["id"]))

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_events_are_returned_oldest_first_with_the_task_key(
        self, user_client: APIClient, published: Any
    ) -> None:
        """Claiming then finishing ``extract`` yields two ordered events."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)
        token = claim_task_run(task_run_id=task_run_id, worker_id="w1", lease_seconds=30, now=NOW)
        assert token is not None
        assert finish_task_run(
            task_run_id=task_run_id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token,
            now=NOW,
        )

        response = user_client.get(_events_url(created.data["id"]))

        assert response.status_code == status.HTTP_200_OK
        assert [(e["kind"], e["from_state"], e["to_state"]) for e in response.data] == [
            ("claim", "ready", "running"),
            ("finish", "running", "succeeded"),
        ]
        assert all(e["task_key"] == "extract" for e in response.data)

    def test_reading_another_users_run_is_404_not_403(
        self, user_client: APIClient, other_client: APIClient, published: Any
    ) -> None:
        """The same visibility rule as the run detail: a stranger's run is hidden."""
        created = _trigger(user_client)

        response = other_client.get(_events_url(created.data["id"]))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unauthenticated_caller_is_401(
        self, api_client: APIClient, user_client: APIClient, published: Any
    ) -> None:
        """The event log is not public."""
        created = _trigger(user_client)

        response = api_client.get(_events_url(created.data["id"]))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
