"""The worker's per-message logic, without a broker.

The consumer loop needs RabbitMQ; ``Worker.process`` is the part that decides what
happens to a message and is tested here with fake ack/nack callbacks and a real
(SQLite) task run.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from core.domain.states import TaskRunState
from core.models import TaskRun, WorkflowRun
from core.repositories.transitions import (
    claim_task_run,
    finish_task_run,
    promote_due_retries,
    reclaim_task_run,
)
from core.worker.consumer import Worker
from core.worker.registry import handlers, register, resolve

from tests.conftest import NOW

pytestmark = pytest.mark.django_db

_CALLS: list[str] = []
_SLOW_STARTED = threading.Event()
_SLOW_RELEASE = threading.Event()
_RENEWED = threading.Event()


def _record(config: Mapping[str, Any]) -> None:
    _CALLS.append(str(config.get("tag")))


def _boom(_config: Mapping[str, Any]) -> None:
    raise RuntimeError("handler exploded")


def _slow(_config: Mapping[str, Any]) -> None:
    _SLOW_STARTED.set()
    _SLOW_RELEASE.wait(timeout=5)


register("test.record", _record)
register("test.boom", _boom)
register("test.slow", _slow)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    _CALLS.clear()
    _SLOW_STARTED.clear()
    _SLOW_RELEASE.clear()
    _RENEWED.clear()


def _payload(
    task_run: TaskRun, *, task_type: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "dispatch_id": "d1",
        "task_run_id": str(task_run.id),
        "task_type": task_type,
        "config": config or {},
        "fencing_token": task_run.fencing_token,
    }


def _worker(lease_seconds: int = 30) -> Worker:
    return Worker(url="fake://", lease_seconds=lease_seconds, worker_id="w1")


def test_success_finishes_succeeded_and_acks(task_run: TaskRun) -> None:
    """A handler that returns leaves the task SUCCEEDED and the message acked."""
    acks: list[bool] = []
    nacks: list[bool] = []

    _worker().process(
        _payload(task_run, task_type="test.record", config={"tag": "x"}),
        ack=lambda: acks.append(True),
        nack=nacks.append,
    )

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.SUCCEEDED.value
    assert _CALLS == ["x"]
    assert acks == [True]
    assert nacks == []


def test_duplicate_delivery_runs_the_handler_once(task_run: TaskRun) -> None:
    """A redelivered message for a finished task is acked without re-executing."""
    worker = _worker()
    payload = _payload(task_run, task_type="test.record", config={"tag": "x"})
    acks: list[bool] = []

    worker.process(payload, ack=lambda: acks.append(True), nack=lambda _requeue: None)
    worker.process(payload, ack=lambda: acks.append(True), nack=lambda _requeue: None)

    assert _CALLS == ["x"]
    assert len(acks) == 2


def test_handler_exception_finishes_failed_and_acks(task_run: TaskRun) -> None:
    """A raising handler fails the task; the message is still acked."""
    acks: list[bool] = []

    _worker().process(
        _payload(task_run, task_type="test.boom"),
        ack=lambda: acks.append(True),
        nack=lambda _requeue: None,
    )

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.FAILED.value
    assert acks == [True]


def test_unknown_task_type_fails_without_running_code(task_run: TaskRun) -> None:
    """An unregistered task_type fails the task; nothing is imported or executed."""
    acks: list[bool] = []

    _worker().process(
        _payload(task_run, task_type="not.registered"),
        ack=lambda: acks.append(True),
        nack=lambda _requeue: None,
    )

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.FAILED.value
    assert acks == [True]


def test_finish_failure_nacks_for_requeue(task_run: TaskRun) -> None:
    """If the durable write fails, the message is nacked for redelivery, not acked."""
    acks: list[bool] = []
    nacks: list[bool] = []

    with patch("core.worker.consumer.finish_task_run", side_effect=RuntimeError("db down")):
        _worker().process(
            _payload(task_run, task_type="test.record", config={"tag": "x"}),
            ack=lambda: acks.append(True),
            nack=nacks.append,
        )

    assert acks == []
    assert nacks == [True]


def test_stale_finish_is_acked(task_run: TaskRun) -> None:
    """A finish rejected as stale (token mismatch) is acked, not retried forever."""
    acks: list[bool] = []
    nacks: list[bool] = []

    with patch("core.worker.consumer.finish_task_run", return_value=False):
        _worker().process(
            _payload(task_run, task_type="test.record", config={"tag": "x"}),
            ack=lambda: acks.append(True),
            nack=nacks.append,
        )

    assert acks == [True]
    assert nacks == []


def test_killed_worker_result_is_rejected_after_a_reap(task_run: TaskRun) -> None:
    """A worker whose lease was reaped cannot overwrite the new generation's result.

    Worker A claims (token 1), its lease expires, the reaper reclaims it (token 2)
    and routes it into ``RETRYING``. Once the backoff elapses the scheduler promotes
    it to ``READY`` and worker B claims (token 3) and finishes. A's late write with
    token 1 matches zero rows, so the new result stands.
    """
    token_a = claim_task_run(
        task_run_id=task_run.id, worker_id="worker-a", lease_seconds=30, now=NOW
    )
    assert token_a == 1
    TaskRun.objects.filter(pk=task_run.id).update(lease_expires_at=NOW - timedelta(seconds=1))
    assert reclaim_task_run(task_run_id=task_run.id, now=NOW + timedelta(seconds=1))

    # The reaper consumed A's attempt and scheduled a retry; the scheduler makes
    # it dispatchable again once the backoff has elapsed.
    assert promote_due_retries(now=NOW + timedelta(seconds=10)) == 1
    token_b = claim_task_run(
        task_run_id=task_run.id,
        worker_id="worker-b",
        lease_seconds=30,
        now=NOW + timedelta(seconds=11),
    )
    assert token_b == 3

    # The killed worker's write is discarded; the live worker's write is recorded.
    assert (
        finish_task_run(
            task_run_id=task_run.id,
            target=TaskRunState.SUCCEEDED,
            fencing_token=token_a,
            now=NOW + timedelta(seconds=3),
        )
        is False
    )
    assert finish_task_run(
        task_run_id=task_run.id,
        target=TaskRunState.SUCCEEDED,
        fencing_token=token_b,
        now=NOW + timedelta(seconds=3),
    )


def test_malformed_payload_is_dropped(task_run: TaskRun) -> None:
    """A message with no task id can never succeed; it is dropped, not requeued."""
    nacks: list[bool] = []

    _worker().process(
        {"task_type": "test.record"},
        ack=lambda: None,
        nack=nacks.append,
    )

    assert nacks == [False]


@pytest.mark.django_db(transaction=True)
def test_heartbeat_renews_the_lease_while_the_handler_runs(task_run: TaskRun) -> None:
    """The heartbeat extends the lease on an interval while a slow handler runs."""
    renewals: list[int] = []

    def fake_renew(**kwargs: Any) -> bool:
        renewals.append(kwargs["fencing_token"])
        _RENEWED.set()
        return True

    worker = _worker(lease_seconds=3)  # heartbeat interval = 1s
    with patch("core.worker.consumer.renew_lease", side_effect=fake_renew):
        thread = threading.Thread(
            target=lambda: worker.process(
                _payload(task_run, task_type="test.slow"),
                ack=lambda: None,
                nack=lambda _requeue: None,
            )
        )
        thread.start()
        assert _SLOW_STARTED.wait(timeout=5)
        assert _RENEWED.wait(timeout=5)  # event-based; no fixed sleep
        _SLOW_RELEASE.set()
        thread.join(timeout=10)

    assert renewals
    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.SUCCEEDED.value


def test_registry_rejects_blank_and_duplicate() -> None:
    """Registration is explicit: blank and duplicate types are errors, not overrides."""
    with pytest.raises(ValueError):
        register("", _record)
    with pytest.raises(ValueError):
        register("test.record", _record)


def test_registry_resolves_only_registered() -> None:
    """Only registered handlers resolve; anything else is None (trusted-only)."""
    assert resolve("test.record") is _record
    assert resolve("does.not.exist") is None
    assert "test.record" in handlers()


def _returns(config: Mapping[str, Any]) -> dict[str, Any]:
    """A handler with a durable return value, so result persistence is observable."""
    return {"echo": config.get("tag"), "ok": True}


register("test.returns", _returns)


def test_a_successful_handler_result_is_persisted(task_run: TaskRun) -> None:
    """The handler's return value is the task's output; dropping it loses the work."""
    _worker().process(
        _payload(task_run, task_type="test.returns", config={"tag": "x"}),
        ack=lambda: None,
        nack=lambda _requeue: None,
    )

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.SUCCEEDED.value
    assert task_run.result == {"echo": "x", "ok": True}


def test_a_failed_handler_persists_no_result(task_run: TaskRun) -> None:
    """A failed attempt has no output; the next attempt must start clean."""
    _worker().process(
        _payload(task_run, task_type="test.boom"),
        ack=lambda: None,
        nack=lambda _requeue: None,
    )

    task_run.refresh_from_db()
    assert task_run.state == TaskRunState.FAILED.value
    assert task_run.result is None


def test_an_idempotent_dispatch_reuses_a_prior_result(
    task_run: TaskRun, workflow_run: WorkflowRun
) -> None:
    """A second task run with the same key must not execute the handler again.

    This is what makes a redelivered or re-dispatched idempotent task safe: the
    side effect happened once, and the second execution returns the first one's
    recorded output instead of repeating it.
    """
    prior = TaskRun.objects.create(
        workflow_run=WorkflowRun.objects.create(workflow=workflow_run.workflow),
        task=task_run.task,
        state=TaskRunState.SUCCEEDED.value,
        idempotency_key="shared-key",
        result={"echo": "first"},
    )
    TaskRun.objects.filter(pk=task_run.pk).update(idempotency_key="shared-key")
    task_run.refresh_from_db()

    payload = _payload(task_run, task_type="test.returns", config={"tag": "x"})
    payload["idempotency_key"] = "shared-key"

    _worker().process(payload, ack=lambda: None, nack=lambda _requeue: None)

    task_run.refresh_from_db()
    assert _CALLS == []  # the handler did not run a second time
    assert task_run.state == TaskRunState.SUCCEEDED.value
    assert task_run.result == prior.result


def test_a_dispatch_without_an_idempotency_key_always_executes(task_run: TaskRun) -> None:
    """A non-idempotent task must run even when a look-alike result exists."""
    TaskRun.objects.create(
        workflow_run=WorkflowRun.objects.create(workflow=task_run.workflow_run.workflow),
        task=task_run.task,
        state=TaskRunState.SUCCEEDED.value,
        idempotency_key="shared-key",
        result={"echo": "first"},
    )

    _worker().process(
        _payload(task_run, task_type="test.returns", config={"tag": "x"}),
        ack=lambda: None,
        nack=lambda _requeue: None,
    )

    task_run.refresh_from_db()
    assert task_run.result == {"echo": "x", "ok": True}
