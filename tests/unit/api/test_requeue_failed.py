"""Re-running a failed task run through the API.

`POST /task-runs/<id>/requeue/` is the manual retry from a FAILED task. It is a
409 for anything not FAILED, a 404 for a task run the caller cannot see, and a
401 without credentials.
"""

from __future__ import annotations

from typing import Any

from core.domain.states import TaskRunState
from core.models import TaskRun
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient


def _trigger(client: APIClient) -> Any:
    """Trigger ``etl`` v1 and return the response."""
    return client.post(reverse("run-list"), {"name": "etl", "version": 1}, format="json")


def _requeue_url(task_run_id: Any) -> str:
    """The canonical URL of a task-run requeue action."""
    return reverse("task-run-requeue", kwargs={"pk": task_run_id})


def _extract_task_run_id(payload: Any) -> Any:
    """The id of the dependency-free ``extract`` task run in a trigger payload."""
    return next(tr["id"] for tr in payload["task_runs"] if tr["task_key"] == "extract")


def _fail(task_run_id: Any) -> None:
    """Drive a task run into FAILED, as a worker that exhausted its attempts."""
    TaskRun.objects.filter(pk=task_run_id).update(
        state=TaskRunState.FAILED.value, attempt=3, finished_at=timezone.now()
    )


class TestTaskRunRequeue:
    """``POST /task-runs/<id>/requeue/`` re-runs a FAILED task run."""

    def test_re_running_a_failed_task_resets_it(
        self, user_client: APIClient, published: Any
    ) -> None:
        """200, and the task run comes back PENDING with a fresh attempt."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)
        _fail(task_run_id)

        response = user_client.post(_requeue_url(task_run_id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["state"] == "pending"
        assert response.data["attempt"] == 1

    def test_re_running_a_task_that_is_not_failed_is_409(
        self, user_client: APIClient, published: Any
    ) -> None:
        """A READY task is not a recovery target; the caller is told, not ignored."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)  # still READY

        response = user_client.post(_requeue_url(task_run_id))

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_re_running_another_users_task_is_404(
        self, user_client: APIClient, other_client: APIClient, published: Any
    ) -> None:
        """The task run is scoped to the caller's runs, so a stranger's is hidden."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)
        _fail(task_run_id)

        response = other_client.post(_requeue_url(task_run_id))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_an_unauthenticated_caller_is_401(
        self, api_client: APIClient, user_client: APIClient, published: Any
    ) -> None:
        """The retry action is not public."""
        created = _trigger(user_client)
        task_run_id = _extract_task_run_id(created.data)
        _fail(task_run_id)

        response = api_client.post(_requeue_url(task_run_id))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
