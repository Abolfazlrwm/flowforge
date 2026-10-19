"""Shared pytest fixtures and configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from core.domain.states import TaskRunState
from core.models import Task, TaskRun, Workflow, WorkflowRun
from rest_framework.test import APIClient

if TYPE_CHECKING:
    from core.models import Task as TaskModel
    from core.models import TaskRun as TaskRunModel
    from core.models import Workflow as WorkflowModel
    from core.models import WorkflowRun as WorkflowRunModel

#: A fixed instant, so assertions about timestamps do not depend on the clock.
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def api_client() -> APIClient:
    """Return a DRF APIClient."""
    return APIClient()


@pytest.fixture
def workflow(db: None) -> WorkflowModel:  # noqa: ARG001 - `db` enables database access
    """A persisted workflow definition."""
    return Workflow.objects.create(name="etl", version=1)


@pytest.fixture
def task(workflow: WorkflowModel) -> TaskModel:
    """A persisted task belonging to :func:`workflow`."""
    return Task.objects.create(workflow=workflow, key="extract", task_type="extract")


@pytest.fixture
def workflow_run(workflow: WorkflowModel) -> WorkflowRunModel:
    """A persisted workflow run in ``PENDING``."""
    return WorkflowRun.objects.create(workflow=workflow)


@pytest.fixture
def task_run(workflow_run: WorkflowRunModel, task: TaskModel) -> TaskRunModel:
    """A persisted task run in ``READY`` — claimable without further setup."""
    return TaskRun.objects.create(
        workflow_run=workflow_run,
        task=task,
        state=TaskRunState.READY.value,
    )
