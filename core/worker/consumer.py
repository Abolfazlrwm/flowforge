"""The worker: consume a dispatch, claim the task run, execute it, finish, ack.

Reliability contract:

- **Manual ack.** A message is acked only after ``finish_task_run`` committed the
  durable outcome. A crash before the ack leaves the message unacked, so the broker
  redelivers it (at-least-once).
- **Duplicate delivery.** ``claim_task_run`` only matches a READY row, so a
  redelivered message for an already-claimed or finished task is acked without
  re-executing.
- **Stale result.** ``finish_task_run`` requires the claim's fencing token. A worker
  whose lease was reaped writes zero rows; the result is rejected and the message is
  acked, because the new generation owns the task.
- **Heartbeat.** A background thread extends the lease while the handler runs, so a
  long task is not reaped mid-flight. If the lease was already lost, the heartbeat
  stops and the finish is correctly rejected as stale.
- **Graceful shutdown.** ``stop()`` halts consumption; the in-flight message
  finishes and is acked, then the connection closes. Unacked messages are redelivered.
- **Trusted handlers only.** The message names a ``task_type``; the handler is
  looked up in the registry. Nothing in the message is imported or executed as code.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import pika

from core.broker_rabbit import DEFAULT_EXCHANGE
from core.domain.states import TaskRunState
from core.models import TaskRun
from core.repositories.transitions import claim_task_run, finish_task_run, renew_lease
from core.worker.registry import resolve

#: The queue every worker consumes from. Bound to the dispatch exchange.
DEFAULT_QUEUE = "flowforge.tasks"
#: The routing key the scheduler's outbox rows are published under.
DEFAULT_ROUTING_KEY = "task.dispatch"


class Worker:
    """A single-threaded consumer with manual ack and a lease heartbeat."""

    def __init__(
        self,
        *,
        url: str,
        queue: str = DEFAULT_QUEUE,
        prefetch: int = 1,
        lease_seconds: int = 30,
        worker_id: str | None = None,
    ) -> None:
        if prefetch <= 0:
            raise ValueError(f"prefetch must be positive, got {prefetch}")
        if lease_seconds <= 0:
            raise ValueError(f"lease_seconds must be positive, got {lease_seconds}")
        self._url = url
        self._queue = queue
        self._prefetch = prefetch
        self._lease_seconds = lease_seconds
        self._worker_id = worker_id or f"worker-{uuid4().hex[:8]}"
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.adapters.blocking_connection.BlockingChannel | None = None
        self._stop = threading.Event()

    @property
    def worker_id(self) -> str:
        """The identity recorded on the lease."""
        return self._worker_id

    @property
    def queue(self) -> str:
        """The queue this worker consumes from."""
        return self._queue

    # -- connection -------------------------------------------------------

    def _connect(self) -> pika.adapters.blocking_connection.BlockingChannel:
        connection = pika.BlockingConnection(pika.URLParameters(self._url))
        channel = connection.channel()
        channel.exchange_declare(exchange=DEFAULT_EXCHANGE, exchange_type="direct", durable=True)
        channel.queue_declare(queue=self._queue, durable=True)
        channel.queue_bind(
            queue=self._queue,
            exchange=DEFAULT_EXCHANGE,
            routing_key=DEFAULT_ROUTING_KEY,
        )
        # Prefetch bounds unacked messages per worker: with 1, a worker holds at
        # most one task, so a crash loses at most one (redelivered) message.
        channel.basic_qos(prefetch_count=self._prefetch)
        self._connection = connection
        self._channel = channel
        return channel

    def run(self) -> None:
        """Consume until ``stop()`` is called, then close the connection."""
        channel = self._connect()
        channel.basic_consume(
            queue=self._queue,
            on_message_callback=self._on_message,
            auto_ack=False,
        )
        try:
            channel.start_consuming()
        finally:
            self.close()

    def stop(self) -> None:
        """Ask the consumer to stop after the in-flight message is acked."""
        self._stop.set()
        if self._connection is not None and self._connection.is_open:
            self._connection.add_callback_threadsafe(self._stop_consuming)

    def _stop_consuming(self) -> None:
        if self._channel is not None and self._channel.is_open:
            self._channel.stop_consuming()

    def close(self) -> None:
        """Close the connection if open."""
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None

    # -- message handling -------------------------------------------------

    def _on_message(
        self,
        channel: pika.adapters.blocking_connection.BlockingChannel,
        method: pika.spec.Basic.Deliver,
        _properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        delivery_tag = method.delivery_tag
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            # Unparseable: it can never succeed, so drop it rather than loop.
            channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
            return
        self.process(
            payload,
            ack=lambda: channel.basic_ack(delivery_tag=delivery_tag),
            nack=lambda requeue: channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue),
        )

    def process(
        self,
        payload: Mapping[str, Any],
        *,
        ack: Callable[[], None],
        nack: Callable[[bool], None],
    ) -> None:
        """Handle one dispatch. Separated from pika so it is unit-testable."""
        task_type = payload.get("task_type")
        if not task_type:
            nack(False)
            return
        try:
            task_run_id = UUID(str(payload["task_run_id"]))
        except (KeyError, ValueError, TypeError):
            # A message without a valid task id can never succeed; drop it.
            nack(False)
            return

        token = claim_task_run(
            task_run_id=task_run_id,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
        )
        if token is None:
            # Duplicate delivery, or another worker owns it. Nothing to do.
            ack()
            return

        handler = resolve(task_type)
        if handler is None:
            # Trusted-only: an unknown type fails the task; no code is imported.
            self._finish(task_run_id, TaskRunState.FAILED, token, ack=ack, nack=nack)
            return

        # Idempotent handler: if this dispatch carries an idempotency key and a
        # prior task run with that key already succeeded, reuse its persisted
        # result and skip executing the handler a second time.
        idempotency_key = payload.get("idempotency_key")
        if idempotency_key:
            prior = self._prior_result(idempotency_key=idempotency_key, task_run_id=task_run_id)
            if prior is not None:
                self._finish(
                    task_run_id,
                    TaskRunState.SUCCEEDED,
                    token,
                    result=prior,
                    ack=ack,
                    nack=nack,
                )
                return

        with self._heartbeat(task_run_id, token):
            try:
                result = handler(payload.get("config", {}))
            except Exception:
                target = TaskRunState.FAILED
                result = None
            else:
                target = TaskRunState.SUCCEEDED
        self._finish(task_run_id, target, token, result=result, ack=ack, nack=nack)

    def _prior_result(self, *, idempotency_key: str, task_run_id: UUID) -> object | None:
        """Return a prior task run's persisted result for a shared idempotency key.

        Looks for a *different* task run that already succeeded with a non-null
        result under the same key. This is best-effort idempotency: it catches the
        common case (the first execution has committed before the duplicate is
        dispatched) but does not serialise two concurrent executions of the same
        key — request-level de-duplication is the API's ``Idempotency-Key`` header.
        """
        prior = (
            TaskRun.objects.filter(
                idempotency_key=idempotency_key,
                state=TaskRunState.SUCCEEDED.value,
                result__isnull=False,
            )
            .exclude(pk=task_run_id)
            .only("result")
            .first()
        )
        return prior.result if prior is not None else None

    def _finish(  # noqa: PLR0913 - the six parameters are the ack contract itself
        self,
        task_run_id: UUID,
        target: TaskRunState,
        token: int,
        *,
        result: object | None = None,
        ack: Callable[[], None],
        nack: Callable[[bool], None],
    ) -> None:
        try:
            finish_task_run(
                task_run_id=task_run_id,
                target=target,
                fencing_token=token,
                result=result,
            )
        except Exception:
            # The durable write failed: do not ack; requeue for another attempt.
            nack(True)
            return
        # Durable (or correctly rejected as stale). Ack.
        ack()

    @contextmanager
    def _heartbeat(self, task_run_id: UUID, token: int) -> Iterator[None]:
        """Extend the lease every ``lease_seconds / 3`` while the handler runs."""
        stop = threading.Event()

        def beat() -> None:
            interval = max(1.0, self._lease_seconds / 3)
            while not stop.wait(interval):
                if not renew_lease(
                    task_run_id=task_run_id,
                    fencing_token=token,
                    lease_seconds=self._lease_seconds,
                ):
                    # Lost the lease (reaped). Stop; the finish will be stale.
                    return

        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5.0)
