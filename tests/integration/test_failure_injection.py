"""Failure injection against real PostgreSQL and RabbitMQ.

Every test here kills or breaks something on purpose and then asserts that the
system *recovers without losing or duplicating work*. They are the evidence behind
``docs/architecture/failure-model.md``; a failure mode listed there without a
named test in this file is marked UNVERIFIED.

The pattern in each test is the same:

1. Put the system into a specific, observable state.
2. Break it (SIGKILL a process, drop a publisher confirm).
3. Assert the durable state is still safe — nothing lost, nothing half-written.
4. Let the recovery mechanism run and assert it finishes the work exactly once.

No arbitrary sleeps: every wait polls a predicate with a deadline.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from typing import IO

import pytest
from core.broker import publish_once
from core.broker_rabbit import RabbitPublisher
from core.domain.states import TaskRunState, WorkflowRunState
from core.models import OutboxMessage, Task, TaskRun, Workflow, WorkflowRun
from core.repositories.outbox import create_dispatch
from core.repositories.scheduler import schedule_once
from core.repositories.transitions import promote_due_retries, reap_expired_leases
from django.conf import settings
from django.utils import timezone

from tests.integration.conftest import (
    bind_queue,
    publish_raw,
    queue_is_empty,
    require_postgres,
    require_rabbitmq,
    subprocess_database_url,
    wait_until,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]


def _state(task_run: TaskRun) -> str:
    task_run.refresh_from_db()
    return task_run.state


def _subprocess_env(url: str) -> dict[str, str]:
    return {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "flowforge.settings.test",
        "DATABASE_URL": subprocess_database_url(),
        "RABBITMQ_URL": url,
        "PYTHONUNBUFFERED": "1",
    }


def _start(
    command: list[str], env: dict[str, str], log_path: Path
) -> tuple[subprocess.Popen[bytes], IO[bytes]]:
    """Start a management command as a child process, logging to ``log_path``.

    Returns the process and its log handle; the caller closes the handle when it
    kills the process, so a failing test can still read the log.
    """
    log = log_path.open("wb")
    proc = subprocess.Popen(  # noqa: S603 - fixed, trusted command
        [sys.executable, "manage.py", *command],
        cwd=str(settings.BASE_DIR),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def _kill(proc: subprocess.Popen[bytes], log: IO[bytes]) -> None:
    """SIGKILL: no handlers run, exactly like a machine losing power."""
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)
    log.close()


def _assert_reaped_and_promoted(task_run: TaskRun, claimed_token: int) -> None:
    """Assert the crash was counted as an attempt, then make the retry dispatchable.

    The reaper is the recovery mechanism. It is polled rather than slept on: the
    lease expires on its own clock, so waiting for the reaper is waiting for the
    lease to lapse, not an arbitrary delay.
    """
    assert wait_until(lambda: reap_expired_leases() > 0, timeout=30)

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.RETRYING.value
    assert task_run.attempt == 2  # the dead attempt was consumed
    assert task_run.fencing_token == claimed_token + 1
    assert task_run.lease_expires_at is None

    # The backoff itself is unit-tested; here we only need the row dispatchable.
    assert promote_due_retries(now=timezone.now() + timedelta(seconds=120)) == 1
    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.READY.value


def test_a_killed_worker_is_reaped_retried_and_finally_succeeds(
    task_run: TaskRun, tmp_path: Path
) -> None:
    """Kill -9 mid-task: the lease is reaped, the attempt is counted, work completes.

    This is the full crash-recovery chain, and each step is a separate guarantee:

    * the row stays ``RUNNING`` after the kill (the worker never wrote a finish),
    * the reaper turns the expired lease into ``RETRYING`` with a *consumed*
      attempt, so a crash loop cannot run forever uncounted,
    * the promoted retry is dispatched again and reaches ``SUCCEEDED``.
    """
    require_postgres()
    url = require_rabbitmq()
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    release_file = tmp_path / "release"
    log_path = tmp_path / "worker.log"

    conn = bind_queue(url, queue)
    try:
        publish_raw(
            url,
            {
                "dispatch_id": uuid.uuid4().hex,
                "task_run_id": str(task_run.id),
                "task_type": "wait_for_file",
                # Long timeout: the handler must still be blocked when the kill lands.
                "config": {"path": str(release_file), "timeout": 120},
                "fencing_token": task_run.fencing_token,
            },
        )

        env = _subprocess_env(url)
        proc, log = _start(
            ["worker", "--queue", queue, "--lease-seconds", "2"],
            env,
            log_path,
        )
        try:
            assert wait_until(lambda: _state(task_run) == TaskRunState.RUNNING.value), (
                log_path.read_text(errors="replace")
            )
            task_run.refresh_from_db()
            assert task_run.lease_expires_at is not None
            claimed_token = task_run.fencing_token

            _kill(proc, log)
        finally:
            if proc.poll() is None:
                _kill(proc, log)

        # The worker died without finishing: the row is still RUNNING, and its
        # lease will expire because nothing is renewing it.
        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.RUNNING.value
        assert task_run.attempt == 1

        _assert_reaped_and_promoted(task_run, claimed_token)

        # Second attempt: the handler's release file already exists, so it returns
        # immediately. The broker may also redeliver the unacked original message;
        # `claim_task_run` only matches READY, so only one of them executes.
        release_file.write_text("go")
        publish_raw(
            url,
            {
                "dispatch_id": uuid.uuid4().hex,
                "task_run_id": str(task_run.id),
                "task_type": "wait_for_file",
                "config": {"path": str(release_file), "timeout": 30},
                "fencing_token": task_run.fencing_token,
            },
        )

        retry_log = tmp_path / "worker-retry.log"
        proc, log = _start(
            ["worker", "--queue", queue, "--lease-seconds", "30"],
            env,
            retry_log,
        )
        try:
            assert wait_until(
                lambda: _state(task_run) == TaskRunState.SUCCEEDED.value, timeout=60
            ), retry_log.read_text(errors="replace")
        finally:
            if proc.poll() is None:
                _kill(proc, log)

        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.SUCCEEDED.value
        assert task_run.attempt == 2
        assert task_run.lease_expires_at is None
        assert wait_until(lambda: queue_is_empty(url, queue), timeout=30)
    finally:
        with contextlib.suppress(Exception):
            conn.channel().queue_delete(queue=queue)
        conn.close()


def test_a_killed_scheduler_cannot_double_dispatch(tmp_path: Path) -> None:
    """Kill -9 the scheduler after it enqueued: the next pass adds no second row.

    The scheduler writes the outbox row and the eligibility change in one
    transaction, and the row is unique per ``(task_run, fencing_token)``. Killing
    the process between those two facts is impossible; killing it after the commit
    leaves a state a fresh pass can safely resume from.
    """
    require_postgres()
    url = require_rabbitmq()

    workflow = Workflow.objects.create(name=f"sched-{uuid.uuid4().hex[:8]}", version=1)
    task = Task.objects.create(workflow=workflow, key="only", task_type="noop")
    run = WorkflowRun.objects.create(workflow=workflow, state=WorkflowRunState.PENDING.value)
    task_run = TaskRun.objects.create(workflow_run=run, task=task, state=TaskRunState.READY.value)

    env = _subprocess_env(url)
    log_path = tmp_path / "scheduler.log"
    proc, log = _start(["scheduler", "--interval", "0.1"], env, log_path)
    try:
        assert wait_until(
            lambda: OutboxMessage.objects.filter(task_run=task_run).exists(), timeout=30
        ), log_path.read_text(errors="replace")
        _kill(proc, log)
    finally:
        if proc.poll() is None:
            _kill(proc, log)

    rows_after_kill = OutboxMessage.objects.filter(task_run=task_run).count()
    assert rows_after_kill == 1

    # A fresh scheduler pass must not enqueue the same generation twice.
    schedule_once()

    assert OutboxMessage.objects.filter(task_run=task_run).count() == rows_after_kill


class _LostAckPublisher:
    """Delivers to the broker, then reports the confirm as lost.

    This is the "message sent, acknowledgement never arrived" failure: the broker
    really has the message, but the publisher cannot know that. The correct
    behaviour is to leave the outbox row pending (at-least-once) rather than mark
    it delivered (which would be at-most-once and could lose the task).
    """

    def __init__(self, inner: RabbitPublisher) -> None:
        self._inner = inner

    def publish(self, *, exchange: str, routing_key: str, payload: dict[str, object]) -> None:
        self._inner.publish(exchange=exchange, routing_key=routing_key, payload=payload)
        raise ConnectionError("connection lost before the publisher confirm arrived")

    def close(self) -> None:
        self._inner.close()


def test_a_lost_publisher_confirm_keeps_the_outbox_row_pending(
    task_run: TaskRun,
) -> None:
    """A broker outage or lost ack must leave the row redeliverable, never lost.

    The outbox is only stamped *after* the broker confirms, so a confirm that
    never arrives leaves the row pending and a later sweep publishes it again.
    At-least-once, never at-most-once.
    """
    require_postgres()
    url = require_rabbitmq()

    message = create_dispatch(
        task_run=task_run,
        workflow_run=task_run.workflow_run,
        fencing_token_snapshot=task_run.fencing_token,
        attempt_snapshot=task_run.attempt,
    )
    assert message is not None

    broken = _LostAckPublisher(RabbitPublisher(url))
    try:
        with pytest.raises(ConnectionError):
            publish_once(broken)
    finally:
        broken.close()

    message.refresh_from_db()
    assert message.published_at is None, "a lost confirm must not mark the row delivered"

    # The broker is healthy again: the same row is delivered and then stamped.
    publisher = RabbitPublisher(url)
    try:
        assert publish_once(publisher) == 1
    finally:
        publisher.close()

    message.refresh_from_db()
    assert message.published_at is not None

    # And it is not delivered a third time.
    publisher = RabbitPublisher(url)
    try:
        assert publish_once(publisher) == 0
    finally:
        publisher.close()
