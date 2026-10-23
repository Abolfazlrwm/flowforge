"""Correlation ids for structured logs.

One id follows a request from the API through the outbox, the broker and the
worker. It is stored on the run and copied into every dispatch, so a log line in
the worker can be tied to the HTTP request that started it even though the
request's process is long gone.

``django-structlog`` already puts a ``request_id`` in the log context (from
``X-Request-ID`` or a generated UUID). That value is the correlation id when it
exists; a background path with no request generates one.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog


def new_correlation_id() -> str:
    """A fresh correlation id."""
    return uuid.uuid4().hex


def current_correlation_id() -> str:
    """The id already in the log context, or a new one.

    ``request_id`` is accepted as a fallback because that is the name
    ``django-structlog`` binds before any of our code runs.
    """
    context = structlog.contextvars.get_contextvars()
    existing = context.get("correlation_id") or context.get("request_id")
    return str(existing) if existing else new_correlation_id()


def bind_request_context(**values: Any) -> None:
    """Bind non-empty values to the current log context.

    ``None`` is dropped rather than bound as the string ``"None"``: a log field
    that says "None" is worse than no field, because it looks like data.
    """
    clean = {key: str(value) for key, value in values.items() if value is not None}
    if clean:
        structlog.contextvars.bind_contextvars(**clean)


def current_organization_id() -> str | None:
    """The organization id in the current log context, if any."""
    context = structlog.contextvars.get_contextvars()
    return context.get("organization_id")


def clear_request_context() -> None:
    """Clear the context. Used by the worker between messages."""
    structlog.contextvars.clear_contextvars()
