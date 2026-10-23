"""The database refuses states the domain considers impossible.

These write through ``QuerySet.update()`` and raw model construction, bypassing
every Python-level guard. That is the point: the constraint has to hold against
a buggy caller, a concurrent writer, a data migration or a hand-written
``UPDATE`` — not just against code that goes through the repository.

If a constraint were only declared in ``Meta.constraints`` and never migrated,
these tests would pass vacuously. Each one therefore asserts that the *write*
fails, not that the declaration exists.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import Task, TaskRun, Workflow, WorkflowRun
from django.db import IntegrityError, transaction

from tests.conftest import NOW

pytestmark = pytest.mark.django_db


class TestTaskRunConstraints:
    """Field and state invariants on ``TaskRun``."""

    def test_rejects_an_unknown_state(self, task_run: TaskRun) -> None:
        """The state column is a closed vocabulary."""
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskRun.objects.filter(pk=task_run.pk).update(state="teleported")

    def test_rejects_a_zero_attempt(self, task_run: TaskRun) -> None:
        """Attempts are 1-based."""
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskRun.objects.filter(pk=task_run.pk).update(attempt=0)

    def test_rejects_a_negative_fencing_token(self, task_run: TaskRun) -> None:
        """A negative token would break the monotonic ordering."""
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskRun.objects.filter(pk=task_run.pk).update(fencing_token=-1)

    def test_rejects_finished_before_started(self, task_run: TaskRun) -> None:
        """Negative durations are a bug the schema should catch."""
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskRun.objects.filter(pk=task_run.pk).update(
                started_at=NOW,
                finished_at=NOW - timedelta(seconds=1),
            )

    def test_rejects_a_lease_on_a_non_running_task(self, task_run: TaskRun) -> None:
        """A lease on an idle row would never be reclaimed."""
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskRun.objects.filter(pk=task_run.pk).update(
                state=TaskRunState.READY.value,
                lease_owner="worker-1",
                lease_expires_at=NOW,
            )

    def test_rejects_a_running_task_without_a_lease(self, task_run: TaskRun) -> None:
        """A running row with no lease cannot be distinguished from a crashed one."""
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskRun.objects.filter(pk=task_run.pk).update(
                state=TaskRunState.RUNNING.value,
            )

    def test_allows_a_running_task_with_a_lease(self, task_run: TaskRun) -> None:
        """The constraint must permit the state the system actually uses."""
        TaskRun.objects.filter(pk=task_run.pk).update(
            state=TaskRunState.RUNNING.value,
            lease_owner="worker-1",
            lease_expires_at=NOW,
        )
        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RUNNING.value

    def test_rejects_two_task_runs_for_the_same_task(
        self, task_run: TaskRun, workflow_run: WorkflowRun
    ) -> None:
        """Retries reuse the row, so a second row would fork the history."""
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskRun.objects.create(
                workflow_run=workflow_run,
                task=task_run.task,
                state=TaskRunState.PENDING.value,
            )


class TestWorkflowRunConstraints:
    """State/timestamp invariants on ``WorkflowRun``."""

    def test_rejects_an_unknown_state(self, workflow_run: WorkflowRun) -> None:
        """Closed vocabulary."""
        with pytest.raises(IntegrityError), transaction.atomic():
            WorkflowRun.objects.filter(pk=workflow_run.pk).update(state="halfway")

    def test_rejects_a_terminal_state_without_finished_at(self, workflow_run: WorkflowRun) -> None:
        """Otherwise durations silently skip these rows."""
        with pytest.raises(IntegrityError), transaction.atomic():
            WorkflowRun.objects.filter(pk=workflow_run.pk).update(
                state=WorkflowRunState.SUCCEEDED.value
            )

    def test_rejects_finished_at_on_a_non_terminal_state(self, workflow_run: WorkflowRun) -> None:
        """A running run with a finish time looks done to anything sorting by it."""
        with pytest.raises(IntegrityError), transaction.atomic():
            WorkflowRun.objects.filter(pk=workflow_run.pk).update(
                state=WorkflowRunState.RUNNING.value,
                finished_at=NOW,
            )

    def test_allows_a_terminal_state_with_finished_at(self, workflow_run: WorkflowRun) -> None:
        """The permitted combination."""
        WorkflowRun.objects.filter(pk=workflow_run.pk).update(
            state=WorkflowRunState.SUCCEEDED.value,
            finished_at=NOW,
        )
        workflow_run.refresh_from_db()
        assert workflow_run.state == WorkflowRunState.SUCCEEDED.value


class TestWorkflowConstraints:
    """Definition identity."""

    def test_rejects_a_duplicate_name_and_version(self, workflow: Workflow) -> None:
        """Publishing a new definition must create a new version, not overwrite."""
        with pytest.raises(IntegrityError), transaction.atomic():
            Workflow.objects.create(
                organization=workflow.organization,
                name=workflow.name,
                version=workflow.version,
            )

    def test_allows_the_same_name_at_a_new_version(self, workflow: Workflow) -> None:
        """Versioning is the supported way to change a definition."""
        second = Workflow.objects.create(
            organization=workflow.organization,
            name=workflow.name,
            version=workflow.version + 1,
        )
        assert second.pk != workflow.pk

    def test_rejects_a_zero_version(self) -> None:
        """Versions start at 1."""
        with pytest.raises(IntegrityError), transaction.atomic():
            Workflow.objects.create(name="fresh", version=0)


class TestTaskConstraints:
    """Step identity within a definition."""

    def test_rejects_a_duplicate_key_in_the_same_workflow(
        self, workflow: Workflow, task: Task
    ) -> None:
        """A run resolves a step by key, so the key must be unambiguous."""
        with pytest.raises(IntegrityError), transaction.atomic():
            Task.objects.create(workflow=workflow, key=task.key)

    def test_allows_the_same_key_in_a_different_workflow(self, task: Task) -> None:
        """Keys are scoped to the definition."""
        other = Workflow.objects.create(name="other", version=1)
        assert Task.objects.create(workflow=other, key=task.key).pk is not None
