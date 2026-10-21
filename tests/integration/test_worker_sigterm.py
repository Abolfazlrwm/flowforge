"""A real SIGTERM to a worker subprocess.

The other worker tests call ``Worker.stop()`` directly. This one starts
``manage.py worker`` as a separate process against the real PostgreSQL and
RabbitMQ, holds a task in flight (its handler blocks until a file appears), sends
SIGTERM, and asserts the worker finishes and acks that task before exiting 0 — the
behaviour a rolling deploy depends on.

Linux only in practice (the CI runner); Windows has no SIGTERM-with-handler
semantics, and the test is gated on the broker and PostgreSQL anyway.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from core.domain.states import TaskRunState
from core.models import TaskRun
from django.conf import settings

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


def test_sigterm_finishes_the_in_flight_task_and_exits_cleanly(
    task_run: TaskRun, tmp_path: Path
) -> None:
    """SIGTERM while a handler runs: finish, ack, exit 0, no live lease left."""
    require_postgres()
    url = require_rabbitmq()
    queue = f"flowforge.test.{uuid.uuid4().hex}"
    release_file = tmp_path / "release"
    log_path = tmp_path / "worker.log"

    # Bind the queue before publishing, so the message waits for the worker
    # instead of racing the worker's own queue declaration.
    conn = bind_queue(url, queue)
    try:
        payload: dict[str, object] = {
            "dispatch_id": uuid.uuid4().hex,
            "task_run_id": str(task_run.id),
            "task_type": "wait_for_file",
            "config": {"path": str(release_file), "timeout": 60},
            "fencing_token": task_run.fencing_token,
        }
        publish_raw(url, payload)

        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "flowforge.settings.test",
            "DATABASE_URL": subprocess_database_url(),
            "RABBITMQ_URL": url,
            "PYTHONUNBUFFERED": "1",
        }
        returncode: int | None = None
        with log_path.open("wb") as log:
            proc = subprocess.Popen(  # noqa: S603 - fixed, trusted command
                [
                    sys.executable,
                    "manage.py",
                    "worker",
                    "--queue",
                    queue,
                    "--lease-seconds",
                    "30",
                ],
                cwd=str(settings.BASE_DIR),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                # The worker claims the task; the handler then blocks on the file.
                assert wait_until(lambda: _state(task_run) == TaskRunState.RUNNING.value)
                task_run.refresh_from_db()
                assert task_run.lease_expires_at is not None

                proc.send_signal(signal.SIGTERM)

                # Release the handler. The worker must finish and ack it, then exit.
                release_file.write_text("go")

                assert wait_until(lambda: proc.poll() is not None, timeout=60)
                returncode = proc.returncode
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)

        assert returncode == 0, log_path.read_text(errors="replace")

        # The in-flight task finished, was acked, and left no live lease.
        task_run.refresh_from_db()
        assert task_run.state == TaskRunState.SUCCEEDED.value
        assert task_run.lease_expires_at is None
        assert not TaskRun.objects.filter(
            state=TaskRunState.RUNNING.value, lease_expires_at__isnull=False
        ).exists()
        assert wait_until(lambda: queue_is_empty(url, queue))
    finally:
        # The channel may already be closed; cleanup is best-effort.
        with contextlib.suppress(Exception):
            conn.channel().queue_delete(queue=queue)
        conn.close()
