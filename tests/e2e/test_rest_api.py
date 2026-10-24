"""The full user journey over the raw REST API.

Deliberately independent of the SDK: these calls use ``urllib`` directly, so a
bug in the client cannot mask a bug in the server (or the reverse). The SDK and
CLI suites cover those surfaces.
"""

from __future__ import annotations

import pytest

from ._support import request, sample_definition, unique_name, wait_for_run

pytestmark = pytest.mark.e2e


def test_publish_then_trigger_then_complete(live, api_token):
    name = unique_name("e2e-rest")
    status, workflow = request("POST", "/workflows/", tok=api_token, body=sample_definition(name))
    assert status in (200, 201), (status, workflow)

    status, run = request("POST", "/runs/", tok=api_token, body={"name": name, "version": 1})
    assert status in (200, 201, 202), (status, run)

    final = wait_for_run(run["id"], tok=api_token, root=live)
    assert final["state"] == "succeeded", final


def test_a_completed_run_writes_an_event_log(live, api_token):
    name = unique_name("e2e-rest-events")
    request("POST", "/workflows/", tok=api_token, body=sample_definition(name))
    _, run = request("POST", "/runs/", tok=api_token, body={"name": name, "version": 1})
    wait_for_run(run["id"], tok=api_token, root=live)

    status, events = request("GET", f"/runs/{run['id']}/events/", tok=api_token)
    assert status == 200, (status, events)
    rows = events.get("results", events) if isinstance(events, dict) else events
    assert rows, "a completed run must expose an event log"


def test_the_published_workflow_is_listed(live, api_token):
    name = unique_name("e2e-rest-list")
    request("POST", "/workflows/", tok=api_token, body=sample_definition(name))

    status, page = request("GET", "/workflows/", tok=api_token)
    assert status == 200, (status, page)
    rows = page.get("results", page) if isinstance(page, dict) else page
    assert any(row.get("name") == name for row in rows), "the new workflow is not listed"


def test_an_unauthenticated_trigger_is_rejected(live):
    status, _ = request("POST", "/runs/", tok="", body={"name": "anything", "version": 1})
    assert status in (401, 403), status
