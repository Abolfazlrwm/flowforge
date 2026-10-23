"""The CLI against a fake transport.

The CLI is a thin layer, so these tests are about the two things a thin layer can
still get wrong: exit codes and output shape. A script branches on the exit code,
and a human reads the table.
"""

from __future__ import annotations

import json

import pytest
from flowforge_client import cli

from .conftest import FakeTransport, json_response

BASE = "http://api.test"


def run_payload(state: str = "succeeded") -> dict[str, object]:
    """A run response body."""
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "workflow_name": "etl",
        "workflow_version": 1,
        "state": state,
        "created_at": "2026-10-23T10:00:00Z",
        "started_at": "2026-10-23T10:00:01Z",
        "finished_at": "2026-10-23T10:00:02Z",
        "task_runs": [
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "task_key": "extract",
                "state": "succeeded",
                "attempt": 1,
                "fencing_token": 0,
                "created_at": "2026-10-23T10:00:00Z",
            }
        ],
    }


def workflow_payload() -> dict[str, object]:
    """A published-workflow response body."""
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "etl",
        "version": 1,
        "description": "Extract and load.",
        "created_at": "2026-10-23T10:00:00Z",
        "tasks": [
            {"key": "extract", "task_type": "echo", "depends_on": [], "config": {}},
        ],
    }


@pytest.fixture
def patched_transport(monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    """Route every client the CLI builds through one fake transport."""
    transport = FakeTransport()
    original = cli.FlowForge

    def factory(base_url: str, **kwargs: object) -> object:
        kwargs["transport"] = transport
        kwargs["sleep"] = lambda _seconds: None
        return original(base_url, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "FlowForge", factory)
    monkeypatch.setenv("FLOWFORGE_URL", BASE)
    monkeypatch.setenv("FLOWFORGE_TOKEN", "tkn")
    return transport


def test_no_url_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, capsys: object) -> None:
    """A CLI that guesses a host is worse than one that refuses."""
    monkeypatch.delenv("FLOWFORGE_URL", raising=False)
    with pytest.raises(SystemExit) as caught:
        cli.main(["workflows"])
    assert caught.value.code == 2


def test_health_returns_zero_when_ready(patched_transport: FakeTransport) -> None:
    patched_transport._rules.append(json_response(200, {"status": "ready", "postgres": "ok"}))
    assert cli.main(["health"]) == 0


def test_health_returns_one_when_degraded(patched_transport: FakeTransport) -> None:
    patched_transport._rules.append(json_response(503, {"status": "degraded", "rabbitmq": "error"}))
    assert cli.main(["health"]) == 1


def test_workflows_prints_a_table(patched_transport: FakeTransport, capsys: object) -> None:
    patched_transport._rules.append(
        json_response(200, {"results": [workflow_payload()], "next": None, "previous": None})
    )
    assert cli.main(["workflows"]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "NAME" in out and "VERSION" in out and "etl" in out


def test_show_prints_the_tasks(patched_transport: FakeTransport, capsys: object) -> None:
    patched_transport._rules.append(json_response(200, workflow_payload()))
    assert cli.main(["show", "etl"]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "etl v1" in out and "extract" in out


def test_trigger_waits_and_returns_zero_on_success(
    patched_transport: FakeTransport, capsys: object
) -> None:
    patched_transport._rules.extend(
        [
            json_response(202, run_payload("pending")),
            json_response(200, run_payload("succeeded")),
        ]
    )
    assert cli.main(["trigger", "etl"]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "triggered run" in out and "succeeded" in out


def test_trigger_returns_one_on_a_failed_run(patched_transport: FakeTransport) -> None:
    patched_transport._rules.extend(
        [
            json_response(202, run_payload("pending")),
            json_response(200, run_payload("failed")),
        ]
    )
    assert cli.main(["trigger", "etl"]) == 1


def test_trigger_sends_an_idempotency_key(patched_transport: FakeTransport) -> None:
    patched_transport._rules.extend(
        [
            json_response(202, run_payload("pending")),
            json_response(200, run_payload("succeeded")),
        ]
    )
    cli.main(["trigger", "etl"])
    assert "Idempotency-Key" in patched_transport.requests[0][2]


def test_trigger_reuses_a_given_key(patched_transport: FakeTransport) -> None:
    patched_transport._rules.extend(
        [
            json_response(202, run_payload("pending")),
            json_response(200, run_payload("succeeded")),
        ]
    )
    cli.main(["trigger", "etl", "--idempotency-key", "fixed"])
    assert patched_transport.requests[0][2]["Idempotency-Key"] == "fixed"


def test_trigger_no_wait_returns_after_accepting(patched_transport: FakeTransport) -> None:
    patched_transport._rules.append(json_response(202, run_payload("pending")))
    assert cli.main(["trigger", "etl", "--no-wait"]) == 0
    # Only the trigger request; no poll followed.
    assert patched_transport.calls == 1


def test_json_flag_prints_raw_payload(patched_transport: FakeTransport, capsys: object) -> None:
    patched_transport._rules.append(json_response(200, run_payload("succeeded")))
    assert cli.main(["--json", "status", "some-id"]) == 0
    payload = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert payload["state"] == "succeeded"


def test_status_returns_one_for_a_failed_run(patched_transport: FakeTransport) -> None:
    patched_transport._rules.append(json_response(200, run_payload("failed")))
    assert cli.main(["status", "some-id"]) == 1


def test_an_api_error_exits_one_with_a_message(
    patched_transport: FakeTransport, capsys: object
) -> None:
    patched_transport._rules.append(
        json_response(404, {"error": {"code": "not_found", "message": "no such run"}})
    )
    assert cli.main(["status", "missing"]) == 1
    assert "no such run" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_events_prints_the_log(patched_transport: FakeTransport, capsys: object) -> None:
    patched_transport._rules.append(
        json_response(
            200,
            {
                "results": [
                    {
                        "id": "e1",
                        "task_key": "extract",
                        "kind": "promote",
                        "from_state": "pending",
                        "to_state": "ready",
                        "created_at": "2026-10-23T10:00:00Z",
                    }
                ]
            },
        )
    )
    assert cli.main(["events", "some-id"]) == 0
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "promote" in out and "pending -> ready" in out


def test_requeue_prints_the_new_state(patched_transport: FakeTransport, capsys: object) -> None:
    patched_transport._rules.append(
        json_response(
            200,
            {
                "id": "t1",
                "task_key": "extract",
                "state": "ready",
                "attempt": 2,
                "fencing_token": 1,
            },
        )
    )
    assert cli.main(["requeue", "t1"]) == 0
    assert "ready" in capsys.readouterr().out  # type: ignore[attr-defined]
