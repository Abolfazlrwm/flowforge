"""The full user journey through the Python SDK.

Uses the real ``flowforge_client`` package (placed on the path by the suite's
conftest) against the live stack: publish, an idempotent trigger,
``wait_for_success``, the run detail and the event log.
"""

from __future__ import annotations

import pytest
from flowforge_client import FlowForge, new_idempotency_key

from ._support import sample_definition, unique_name

pytestmark = pytest.mark.e2e


def test_publish_trigger_and_wait_for_success(client: FlowForge):
    name = unique_name("e2e-sdk")
    workflow = client.publish(sample_definition(name))
    assert workflow.name == name

    run = client.trigger(name, version=1, idempotency_key=new_idempotency_key())
    finished = client.wait_for_success(run.id, timeout=150)
    assert finished.state == "succeeded"


def test_an_idempotent_trigger_replays_one_run(client: FlowForge):
    name = unique_name("e2e-sdk-idem")
    client.publish(sample_definition(name))

    key = new_idempotency_key()
    first = client.trigger(name, version=1, idempotency_key=key)
    second = client.trigger(name, version=1, idempotency_key=key)
    assert first.id == second.id, "the same idempotency key must replay one run"


def test_the_sdk_reports_a_runs_event_log(client: FlowForge):
    name = unique_name("e2e-sdk-events")
    client.publish(sample_definition(name))
    run = client.trigger(name, version=1, idempotency_key=new_idempotency_key())
    client.wait_for_success(run.id, timeout=150)

    events = client.run_events(run.id)
    assert events, "a completed run must expose its event log through the SDK"
