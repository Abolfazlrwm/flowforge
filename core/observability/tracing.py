"""OpenTelemetry tracing with W3C trace-context propagation.

The chain is API -> scheduler -> publisher -> worker, and the hard part is the
outbox: the scheduler runs in a different process, minutes later, with no live
span to inherit. The fix is to persist the W3C ``traceparent`` on the run and on
the outbox row, then start the next span as a child of that extracted context.

The provider is module-local rather than the OpenTelemetry global one. The global
provider can only be set once per process, which makes a test that installs an
in-memory exporter order-dependent; a module-local provider can be replaced and
reset freely. No auto-instrumentation is used, so nothing depends on the global
provider.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span, Tracer

_provider: TracerProvider | None = None
_tracer: Tracer | None = None


def configure_tracing(service_name: str, *, exporter: SpanExporter | None = None) -> None:
    """Configure the tracer provider once.

    Args:
        service_name: The ``service.name`` resource attribute, e.g. ``web`` or
            ``worker``. It is what distinguishes the processes in a trace.
        exporter: A span exporter to use directly. Tests pass an in-memory
            exporter; production leaves it ``None`` and the OTLP endpoint from
            ``OTEL_EXPORTER_OTLP_ENDPOINT`` is used when set.
    """
    global _provider, _tracer  # noqa: PLW0603 - module-level provider, set once
    if _tracer is not None:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
            )
    _provider = provider
    _tracer = provider.get_tracer("flowforge")


def reset_tracing_for_tests() -> None:
    """Drop the configured provider so the next test can install its own."""
    global _provider, _tracer  # noqa: PLW0603 - test-only reset
    _provider = None
    _tracer = None


def tracer() -> Tracer:
    """The configured tracer, or the no-op global tracer when unconfigured."""
    return _tracer if _tracer is not None else trace.get_tracer("flowforge")


def current_traceparent() -> str | None:
    """The W3C ``traceparent`` of the active span, or ``None`` outside a span."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent")


def context_from_traceparent(traceparent: str | None) -> Any:
    """Extract a parent context from a stored ``traceparent``."""
    if not traceparent:
        return None
    return extract({"traceparent": traceparent})


@contextmanager
def start_span(
    name: str,
    *,
    context: Any = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Start a span as the current span, optionally as a child of ``context``."""
    with tracer().start_as_current_span(name, context=context, attributes=attributes or {}) as span:
        yield span


def shutdown_tracing() -> None:
    """Flush and stop the provider, if one was configured."""
    if _provider is not None:
        _provider.shutdown()
