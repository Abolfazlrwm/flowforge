"""Tests for the observability stack: correlation, tracing, metrics.

Each subsystem is tested in isolation, then an integration-style test proves
that a trigger's correlation id and traceparent reach the outbox payload.
"""

from __future__ import annotations

import uuid

import pytest
from core.domain.schema import parse_workflow_definition
from core.models import Membership, Organization, Workflow
from core.observability import context as obs_context
from core.observability import metrics as obs_metrics
from core.observability import tracing
from core.repositories.definitions import publish_definition
from core.repositories.outbox import build_dispatch_payload, create_dispatch
from core.services import runs as run_service
from django.contrib.auth.models import User
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.unit.api.payloads import definition_payload


def _parse_workflow(organization: Organization) -> Workflow:
    """Publish the shared ``etl`` v1 definition into ``organization``."""
    workflow, _ = publish_definition(
        parse_workflow_definition(definition_payload()), organization=organization
    )
    return workflow


@pytest.fixture
def org_and_user(db: None) -> tuple[Organization, User]:
    """A tenant with one ordinary member."""
    organization = Organization.objects.create(name="Acme", slug="acme")
    user = User.objects.create_user("alice", password="pw12345!")
    Membership.objects.create(organization=organization, user=user, role=Membership.Role.MEMBER)
    return organization, user


class TestCorrelationContext:
    """Correlation IDs are generated per request and bound to the log context."""

    def test_generate_correlation_id(self) -> None:
        """A fresh id is a 32-character hex string."""
        cid = obs_context.new_correlation_id()
        assert isinstance(cid, str)
        assert len(cid) == 32

    def test_bind_request_context_sets_contextvars(self) -> None:
        """A bound id and organization id become the current context values."""
        cid = obs_context.new_correlation_id()
        obs_context.bind_request_context(correlation_id=cid, organization_id=42)
        assert obs_context.current_correlation_id() == cid
        assert obs_context.current_organization_id() == "42"

    def test_clear_context_unsets_contextvars(self) -> None:
        """Clearing drops the bound id rather than returning it."""
        obs_context.bind_request_context(correlation_id="abc", organization_id=1)
        obs_context.clear_request_context()
        assert obs_context.current_correlation_id() != "abc"


class TestTracing:
    """OpenTelemetry tracing: start, propagate, and export spans."""

    @pytest.fixture(autouse=True)
    def _reset_tracing(self) -> None:
        """Isolate every test from module-level tracer state."""
        tracing.shutdown_tracing()
        tracing.reset_tracing_for_tests()
        yield
        tracing.shutdown_tracing()
        tracing.reset_tracing_for_tests()

    def test_start_span_records_an_exported_span(self, db: None) -> None:
        """A span started inside the context is exported."""
        exporter = InMemorySpanExporter()
        tracing.configure_tracing(service_name="test", exporter=exporter)

        with tracing.start_span("test.op"):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.op"

    def test_traceparent_round_trip(self, db: None) -> None:
        """A stored traceparent yields a child span under its parent."""
        exporter = InMemorySpanExporter()
        tracing.configure_tracing(service_name="test", exporter=exporter)

        with tracing.start_span("parent"):
            tp = tracing.current_traceparent()

        assert tp is not None
        ctx = tracing.context_from_traceparent(tp)
        with tracing.start_span("child", context=ctx):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        child = next(s for s in spans if s.name == "child")
        parent = next(s for s in spans if s.name == "parent")
        assert child.parent.span_id == parent.get_span_context().span_id


class TestMetrics:
    """Prometheus metrics: rendering, counting, and latency observation."""

    @pytest.mark.django_db
    def test_render_metrics_returns_prometheus_text(self) -> None:
        """The rendered body contains the counters that were incremented."""
        body = obs_metrics.render_metrics().decode()
        assert "# HELP" in body or "# TYPE" in body or body == ""
        obs_metrics.TASK_RETRIES.inc()
        body = obs_metrics.render_metrics().decode()
        assert "flowforge_task_retries_total" in body

    @pytest.mark.django_db
    def test_task_latency_observes_a_value(self) -> None:
        """An observed latency appears in the histogram."""
        obs_metrics.TASK_LATENCY.observe(0.123)
        body = obs_metrics.render_metrics().decode()
        assert "flowforge_task_latency_seconds" in body

    @pytest.mark.django_db
    def test_queue_depth_gauge(self) -> None:
        """The queue depth gauge is labelled by queue."""
        obs_metrics.set_queue_depth("flowforge.tasks", 7)
        body = obs_metrics.render_metrics().decode()
        assert "flowforge_queue_depth" in body
        assert 'queue="flowforge.tasks"' in body


class TestTracePropagationIntegration:
    """A triggered run carries correlation and trace into the outbox payload."""

    @pytest.fixture(autouse=True)
    def _reset_tracing(self) -> None:
        """Isolate every test from module-level tracer state."""
        tracing.shutdown_tracing()
        tracing.reset_tracing_for_tests()
        yield
        tracing.shutdown_tracing()
        tracing.reset_tracing_for_tests()

    def test_trigger_stores_correlation_and_traceparent_on_run(
        self, db: None, org_and_user: tuple[Organization, User]
    ) -> None:
        """The correlation id and traceparent survive onto the run row."""
        organization, user = org_and_user
        workflow = _parse_workflow(organization)
        cid = "test-correlation-123"
        tp = "00-12345678901234567890123456789012-1234567890123456-01"

        run, _ = run_service.trigger(
            workflow,
            requested_by_id=user.pk,
            organization=organization,
            correlation_id=cid,
            traceparent=tp,
        )

        assert run.correlation_id == cid
        assert run.traceparent == tp

    def test_outbox_payload_includes_correlation_and_traceparent(
        self, db: None, org_and_user: tuple[Organization, User]
    ) -> None:
        """The dispatch payload carries both identifiers."""
        organization, user = org_and_user
        workflow = _parse_workflow(organization)
        run, _ = run_service.trigger(
            workflow,
            requested_by_id=user.pk,
            organization=organization,
            correlation_id="cid",
            traceparent="tp",
        )
        task_run = run.task_runs.first()
        assert task_run is not None

        payload = build_dispatch_payload(
            dispatch_id=uuid.uuid4(),
            task_run=task_run,
            fencing_token_snapshot=task_run.fencing_token,
            attempt_snapshot=task_run.attempt,
            correlation_id="cid",
            traceparent="tp",
        )

        assert payload["correlation_id"] == "cid"
        assert payload["traceparent"] == "tp"

    def test_create_dispatch_persists_traceparent_on_message(
        self, db: None, org_and_user: tuple[Organization, User]
    ) -> None:
        """The outbox row stores both identifiers, not just the payload."""
        organization, user = org_and_user
        workflow = _parse_workflow(organization)
        run, _ = run_service.trigger(
            workflow,
            requested_by_id=user.pk,
            organization=organization,
            correlation_id="cid",
            traceparent="tp",
        )
        task_run = run.task_runs.first()
        assert task_run is not None

        message = create_dispatch(
            task_run=task_run,
            workflow_run=run,
            fencing_token_snapshot=task_run.fencing_token,
            attempt_snapshot=task_run.attempt,
            correlation_id="cid",
            traceparent="tp",
        )
        assert message is not None
        assert message.correlation_id == "cid"
        assert message.traceparent == "tp"


class TestMetricsPortArgument:
    """The argparse type for ``--metrics-port``.

    A compose file that references an unset variable interpolates it to an empty
    string, so the process receives ``--metrics-port ""``. That must mean
    "disabled", not a ValueError that kills the process at startup — which is
    exactly how the scheduler died in the first CI run of the observability
    stack.
    """

    def test_empty_string_disables_the_endpoint(self) -> None:
        """An empty value means the endpoint is off, not an error."""
        assert obs_metrics.metrics_port_argument("") == 0

    def test_whitespace_disables_the_endpoint(self) -> None:
        """Whitespace is treated the same as empty."""
        assert obs_metrics.metrics_port_argument("   ") == 0

    def test_zero_disables_the_endpoint(self) -> None:
        """Zero is the explicit form of "disabled"."""
        assert obs_metrics.metrics_port_argument("0") == 0

    def test_a_real_port_is_parsed(self) -> None:
        """A real port is parsed to its integer."""
        assert obs_metrics.metrics_port_argument("9101") == 9101

    def test_a_malformed_value_still_raises(self) -> None:
        """A malformed value is still an error, so a typo is not silently ignored."""
        with pytest.raises(ValueError):
            obs_metrics.metrics_port_argument("not-a-port")
