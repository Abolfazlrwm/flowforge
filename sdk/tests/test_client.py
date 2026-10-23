"""The client against a fake transport.

The tests are grouped by what they protect: the request the client builds, how it
reacts to an error envelope, how it retries, how it pages, and how it polls.
"""

from __future__ import annotations

import json
from itertools import pairwise

import pytest
from flowforge_client import (
    AuthenticationError,
    ConflictError,
    FlowForge,
    NotFoundError,
    ServerError,
    ThrottledError,
    TransportError,
    ValidationError,
)
from flowforge_client._transport import Response

from .conftest import FakeTransport, json_response

BASE = "http://api.test"


def client_with(transport: FakeTransport, **kwargs: object) -> FlowForge:
    """A client wired to ``transport`` with a no-op sleep, so no test waits."""
    return FlowForge(BASE, token="tkn", transport=transport, sleep=lambda _s: None, **kwargs)


def workflow_payload(name: str = "etl", version: int = 1) -> dict[str, object]:
    """A published-workflow response body."""
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": name,
        "version": version,
        "description": "d",
        "created_at": "2026-10-23T10:00:00Z",
        "tasks": [
            {"key": "extract", "task_type": "echo", "depends_on": [], "config": {}},
            {"key": "load", "task_type": "noop", "depends_on": ["extract"], "config": {}},
        ],
    }


def run_payload(state: str = "pending") -> dict[str, object]:
    """A run response body with one task run."""
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "workflow_name": "etl",
        "workflow_version": 1,
        "state": state,
        "created_at": "2026-10-23T10:00:00Z",
        "started_at": None,
        "finished_at": None,
        "task_runs": [
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "task_key": "extract",
                "state": "ready",
                "attempt": 1,
                "fencing_token": 0,
                "lease_owner": None,
                "lease_expires_at": None,
                "created_at": "2026-10-23T10:00:00Z",
                "started_at": None,
                "finished_at": None,
            }
        ],
    }


class TestRequests:
    """The client builds the documented request."""

    def test_base_url_gets_the_api_prefix(self) -> None:
        transport = FakeTransport(json_response(200, workflow_payload()))
        client_with(transport).get_workflow("etl", 1)
        assert transport.requests[0][1] == "http://api.test/api/v1/workflows/etl/1/"

    def test_token_is_sent_as_a_drf_token(self) -> None:
        transport = FakeTransport(json_response(200, workflow_payload()))
        client_with(transport).get_workflow("etl", 1)
        assert transport.last_headers()["Authorization"] == "Token tkn"

    def test_publish_posts_the_definition(self) -> None:
        transport = FakeTransport(json_response(201, workflow_payload()))
        definition = {"name": "etl", "version": 1, "tasks": []}
        client_with(transport).publish(definition)
        assert transport.requests[0][0] == "POST"
        assert transport.last_body() == definition

    def test_trigger_posts_name_and_version(self) -> None:
        transport = FakeTransport(json_response(202, run_payload()))
        client_with(transport).trigger("etl", version=2)
        assert transport.requests[0][1].endswith("/api/v1/runs/")
        assert transport.last_body() == {"name": "etl", "version": 2}

    def test_trigger_sends_the_idempotency_key(self) -> None:
        transport = FakeTransport(json_response(202, run_payload()))
        client_with(transport).trigger("etl", idempotency_key="abc123")
        assert transport.last_headers()["Idempotency-Key"] == "abc123"

    def test_trigger_without_a_key_sends_no_header(self) -> None:
        transport = FakeTransport(json_response(202, run_payload()))
        client_with(transport).trigger("etl")
        assert "Idempotency-Key" not in transport.last_headers()

    def test_page_size_becomes_a_query_parameter(self) -> None:
        transport = FakeTransport(
            json_response(200, {"results": [], "next": None, "previous": None})
        )
        list(client_with(transport).list_runs(page_size=5))
        assert "page_size=5" in transport.requests[0][1]

    def test_login_exchanges_credentials_for_a_token(self) -> None:
        transport = FakeTransport(json_response(200, {"token": "fresh"}))
        client = FlowForge.login(
            BASE, username="alice", password="pw", transport=transport, sleep=lambda _s: None
        )
        assert transport.last_body() == {"username": "alice", "password": "pw"}
        # The returned client carries the new token, not the one it started with.
        transport2 = FakeTransport(json_response(200, workflow_payload()))
        client._http.transport = transport2
        client.get_workflow("etl", 1)
        assert transport2.last_headers()["Authorization"] == "Token fresh"


class TestErrorMapping:
    """An error envelope becomes a specific exception."""

    def test_not_found_becomes_not_found_error(self) -> None:
        transport = FakeTransport(
            json_response(
                404,
                {"error": {"code": "not_found", "message": "no such run", "details": {}}},
            )
        )
        with pytest.raises(NotFoundError) as caught:
            client_with(transport).get_run("missing")
        assert caught.value.status_code == 404
        assert caught.value.code == "not_found"
        assert caught.value.message == "no such run"

    def test_401_becomes_authentication_error(self) -> None:
        transport = FakeTransport(
            json_response(401, {"error": {"code": "not_authenticated", "message": "no"}})
        )
        with pytest.raises(AuthenticationError):
            client_with(transport).get_run("x")

    def test_409_becomes_conflict_error(self) -> None:
        transport = FakeTransport(
            json_response(409, {"error": {"code": "definition_conflict", "message": "dup"}})
        )
        with pytest.raises(ConflictError):
            client_with(transport).publish({"name": "etl", "version": 1, "tasks": []})

    def test_422_becomes_validation_error(self) -> None:
        transport = FakeTransport(
            json_response(422, {"error": {"code": "validation_error", "message": "bad"}})
        )
        with pytest.raises(ValidationError):
            client_with(transport).publish({"name": "etl"})

    def test_details_survive(self) -> None:
        transport = FakeTransport(
            json_response(
                400,
                {
                    "error": {
                        "code": "validation_error",
                        "message": "bad",
                        "details": {"name": ["required"]},
                    }
                },
            )
        )
        with pytest.raises(ValidationError) as caught:
            client_with(transport).publish({})
        assert caught.value.details == {"name": ["required"]}

    def test_a_body_with_no_envelope_still_raises(self) -> None:
        """A proxy returning HTML must not produce an AttributeError."""
        transport = FakeTransport(Response(status=502, body="<html>bad gateway</html>", headers={}))
        with pytest.raises(Exception) as caught:
            client_with(transport, max_attempts=1).get_run("x")
        assert "502" in str(caught.value)

    def test_a_5xx_is_never_mapped_to_a_4xx_type(self) -> None:
        """The status decides retryability, so it wins over a misleading code."""
        transport = FakeTransport(
            json_response(500, {"error": {"code": "not_found", "message": "?"}})
        )
        with pytest.raises(Exception) as caught:
            client_with(transport, max_attempts=1).get_run("x")
        assert caught.value.status_code == 500  # type: ignore[attr-defined]


class TestRetries:
    """Only transient failures are retried, and only when a retry is safe."""

    def test_a_500_is_retried_then_succeeds(self) -> None:
        transport = FakeTransport(
            json_response(500, {"error": {"code": "internal", "message": "boom"}}),
            json_response(200, run_payload()),
        )
        run = client_with(transport).get_run("x")
        assert run.state == "pending"
        assert transport.calls == 2

    def test_a_429_is_retried_and_honours_retry_after(self) -> None:
        slept: list[float] = []
        transport = FakeTransport(
            json_response(
                429,
                {"error": {"code": "throttled", "message": "slow down"}},
                Retry_After="7",
            ),
            json_response(200, run_payload()),
        )
        client = FlowForge(BASE, transport=transport, sleep=slept.append)
        client.get_run("x")
        assert slept == [7.0]

    def test_a_404_is_not_retried(self) -> None:
        transport = FakeTransport(
            json_response(404, {"error": {"code": "not_found", "message": "no"}})
        )
        with pytest.raises(NotFoundError):
            client_with(transport).get_run("x")
        assert transport.calls == 1

    def test_a_throttled_error_carries_retry_after_when_attempts_run_out(self) -> None:
        transport = FakeTransport(
            json_response(429, {"error": {"code": "throttled", "message": "slow"}}, Retry_After="3")
        )
        with pytest.raises(ThrottledError) as caught:
            client_with(transport, max_attempts=1).get_run("x")
        assert caught.value.retry_after == 3.0

    def test_a_trigger_without_a_key_is_not_retried(self) -> None:
        """A retried POST without a key would start a second run."""
        transport = FakeTransport(
            json_response(500, {"error": {"code": "internal", "message": "boom"}})
        )
        with pytest.raises(ServerError):
            client_with(transport).trigger("etl")
        assert transport.calls == 1

    def test_a_trigger_with_a_key_is_retried(self) -> None:
        transport = FakeTransport(
            json_response(500, {"error": {"code": "internal", "message": "boom"}}),
            json_response(202, run_payload()),
        )
        client_with(transport).trigger("etl", idempotency_key="k1")
        assert transport.calls == 2

    def test_a_transport_error_is_retried_for_a_read(self) -> None:
        def boom(*_args: object) -> Response:
            raise TransportError("connection reset")

        transport = FakeTransport(boom, json_response(200, run_payload()))
        run = client_with(transport).get_run("x")
        assert run.state == "pending"

    def test_a_transport_error_is_raised_after_the_last_attempt(self) -> None:
        def boom(*_args: object) -> Response:
            raise TransportError("connection reset")

        transport = FakeTransport(boom, boom, boom)
        with pytest.raises(TransportError):
            client_with(transport, max_attempts=3).get_run("x")


class TestPagination:
    """Cursor pagination is followed to the end."""

    def test_every_page_is_read(self) -> None:
        transport = FakeTransport(
            json_response(
                200,
                {
                    "results": [
                        {
                            "id": "1",
                            "workflow_name": "a",
                            "workflow_version": 1,
                            "state": "succeeded",
                        }
                    ],
                    "next": "http://api.test/api/v1/runs/?cursor=abc",
                    "previous": None,
                },
            ),
            json_response(
                200,
                {
                    "results": [
                        {"id": "2", "workflow_name": "a", "workflow_version": 1, "state": "failed"}
                    ],
                    "next": None,
                    "previous": None,
                },
            ),
        )
        runs = list(client_with(transport).list_runs())
        assert [run.id for run in runs] == ["1", "2"]
        # The second request used the URL the server returned, not a rebuilt one.
        assert transport.requests[1][1] == "http://api.test/api/v1/runs/?cursor=abc"

    def test_an_unpaginated_list_is_accepted(self) -> None:
        """A future server that stops paginating must not break the client."""
        transport = FakeTransport(json_response(200, [{"id": "1", "name": "etl", "version": 1}]))
        workflows = list(client_with(transport).list_workflows())
        assert len(workflows) == 1


class TestWait:
    """Polling until a run settles."""

    def test_it_returns_the_settled_run(self) -> None:
        transport = FakeTransport(
            json_response(200, run_payload("running")),
            json_response(200, run_payload("running")),
            json_response(200, run_payload("succeeded")),
        )
        run = client_with(transport).wait("x", timeout=5)
        assert run.succeeded
        assert transport.calls == 3

    def test_it_raises_on_timeout_and_names_the_state(self) -> None:
        transport = FakeTransport(*[json_response(200, run_payload("running"))] * 20)
        with pytest.raises(TimeoutError) as caught:
            client_with(transport).wait("x", timeout=0)
        assert "running" in str(caught.value)

    def test_a_failed_run_is_returned_not_raised(self) -> None:
        """`wait` reports; `wait_for_success` is the one that raises."""
        transport = FakeTransport(json_response(200, run_payload("failed")))
        run = client_with(transport).wait("x", timeout=5)
        assert run.state == "failed"
        assert not run.succeeded

    def test_wait_for_success_raises_on_failure(self) -> None:
        transport = FakeTransport(json_response(200, run_payload("failed")))
        with pytest.raises(Exception) as caught:
            client_with(transport).wait_for_success("x", timeout=5)
        assert "failed" in str(caught.value)

    def test_polling_backs_off(self) -> None:
        """A long run must not cost hundreds of requests."""
        slept: list[float] = []
        transport = FakeTransport(
            *[json_response(200, run_payload("running"))] * 5,
            json_response(200, run_payload("succeeded")),
        )
        client = FlowForge(BASE, transport=transport, sleep=slept.append)
        client.wait("x", timeout=60)
        assert slept[0] < slept[-1]
        assert all(later >= earlier for earlier, later in pairwise(slept))


class TestModels:
    """The parsed models behave like values."""

    def test_timestamps_are_parsed(self) -> None:
        transport = FakeTransport(json_response(200, run_payload()))
        run = client_with(transport).get_run("x")
        assert run.created_at is not None
        assert run.created_at.year == 2026

    def test_task_run_lookup_by_key(self) -> None:
        transport = FakeTransport(json_response(200, run_payload()))
        run = client_with(transport).get_run("x")
        assert run.task_run("extract").state == "ready"

    def test_task_run_lookup_raises_for_an_unknown_key(self) -> None:
        transport = FakeTransport(json_response(200, run_payload()))
        run = client_with(transport).get_run("x")
        with pytest.raises(KeyError):
            run.task_run("nope")

    def test_is_terminal_covers_the_terminal_states(self) -> None:
        for state in ("succeeded", "failed", "cancelled"):
            transport = FakeTransport(json_response(200, run_payload(state)))
            assert client_with(transport).get_run("x").is_terminal
        for state in ("pending", "running"):
            transport = FakeTransport(json_response(200, run_payload(state)))
            assert not client_with(transport).get_run("x").is_terminal

    def test_unknown_fields_are_ignored(self) -> None:
        """A field the server adds must not break an older client."""
        payload = run_payload()
        payload["a_field_from_the_future"] = {"nested": True}
        transport = FakeTransport(json_response(200, payload))
        assert client_with(transport).get_run("x").state == "pending"


class TestHealth:
    """The readiness probe lives outside /api/v1."""

    def test_health_uses_the_site_root(self) -> None:
        transport = FakeTransport(json_response(200, {"status": "ready", "postgres": "ok"}))
        client_with(transport).health()
        assert transport.requests[0][1] == "http://api.test/health/ready/"


def test_json_body_is_encoded_once() -> None:
    """The body must be bytes on the wire, not a dict a transport re-encodes."""
    transport = FakeTransport(json_response(201, workflow_payload()))
    client_with(transport).publish({"name": "etl", "version": 1, "tasks": []})
    body = transport.requests[0][3]
    assert isinstance(body, bytes)
    assert json.loads(body) == {"name": "etl", "version": 1, "tasks": []}
