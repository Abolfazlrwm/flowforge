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
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pika
import pytest
from core.broker_rabbit import DEFAULT_EXCHANGE, RabbitPublisher
from core.domain.states import TaskRunState
from core.models import TaskRun
from core.worker.consumer import DEFAULT_ROUTING_KEY
from django.conf import settings

from tests.integration.conftest import require_postgres, require_rabbitmq

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]


def _wait_until(predicate: Callable[[], bool], timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)  # bounded poll, not a synchronisation sleep
    return False


def _bind_queue(url: str, queue: str) -> pika.BlockingConnection:
    conn = pika.BlockingConnection(pika.URLParameters(url))
    channel = conn.channel()
    channel.exchange_declare(exchange=DEFAULT_EXCHANGE, exchange_type="direct", durable=True)
    channel.queue_declare(queue=queue, durable=True)
    channel.queue_bind(queue=queue, exchange=DEFAULT_EXCHANGE, routing_key=DEFAULT_ROUTING_KEY)
    return conn


def _publish(url: str, payload: dict[str, object]) -> None:
    publisher = RabbitPublisher(url)
    try:
        publisher.publish(
            exchange=DEFAULT_EXCHANGE,
            routing_key=DEFAULT_ROUTING_KEY,
            payload=payload,
        )
    finally:
        publisher.close()


def _queue_is_empty(url: str, queue: str) -> bool:
    conn = pika.BlockingConnection(pika.URLParameters(url))
    try:
        result = conn.channel().queue_declare(queue=queue, passive=True)
        return result.method.message_count == 0
    finally:
        conn.close()


def _state(task_run: TaskRun) -> str:
    task_run.refresh_from_db()
    return task_run.state


def _database_url() -> str:
    """The *test* database, so the subprocess sees the rows this test created."""
    db = settings.DATABASES["default"]
    host = db.get("HOST") or "localhost"
    port = db.get("PORT") or "5432"
    return f"postgres://{db['USER']}:{db['PASSWORD']}@{host}:{port}/{db['NAME']}"


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
    conn = _bind_queue(url, queue)
    try:
        payload: dict[str, object] = {
            "dispatch_id": uuid.uuid4().hex,
            "task_run_id": str(task_run.id),
            "task_type": "wait_for_file",
            "config": {"path": str(release_file), "timeout": 60},
            "fencing_token": task_run.fencing_token,
        }
        _publish(url, payload)

        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "flowforge.settings.test",
            "DATABASE_URL": _database_url(),
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
                assert _wait_until(lambda: _state(task_run) == TaskRunState.RUNNING.value)
                task_run.refresh_from_db()
                assert task_run.lease_expires_at is not None

                proc.send_signal(signal.SIGTERM)

                # Release the handler. The worker must finish and ack it, then exit.
                release_file.write_text("go")

                assert _wait_until(lambda: proc.poll() is not None, timeout=60)
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
        assert _wait_until(lambda: _queue_is_empty(url, queue))
    finally:
        # The channel may already be closed; cleanup is best-effort.
        with contextlib.suppress(Exception):
            conn.channel().queue_delete(queue=queue)
        conn.close()
