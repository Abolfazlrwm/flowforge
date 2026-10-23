"""A dependency-free Python client for the FlowForge REST API.

    from flowforge_client import FlowForge, new_idempotency_key

    with FlowForge("http://localhost:8000", token="ff_...") as client:
        run = client.trigger("etl", version=1, idempotency_key=new_idempotency_key())
        finished = client.wait(run.id, timeout=120)
        if not finished.succeeded:
            raise SystemExit(f"run ended {finished.state}")

The package has no runtime dependencies. It is meant to be installed beside an
application whose dependency set it must not constrain, so it uses
``urllib.request`` rather than an HTTP library.
"""

from __future__ import annotations

from .client import FlowForge, new_idempotency_key
from .errors import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    FlowForgeError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    ThrottledError,
    TransportError,
    ValidationError,
)
from .models import (
    TERMINAL_RUN_STATES,
    TERMINAL_TASK_STATES,
    Event,
    Run,
    RunSummary,
    Task,
    TaskRun,
    Workflow,
    WorkflowSummary,
)

__version__ = "0.11.0"

__all__ = [
    "TERMINAL_RUN_STATES",
    "TERMINAL_TASK_STATES",
    "APIError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "Event",
    "FlowForge",
    "FlowForgeError",
    "ForbiddenError",
    "NotFoundError",
    "Run",
    "RunSummary",
    "ServerError",
    "Task",
    "TaskRun",
    "ThrottledError",
    "TransportError",
    "ValidationError",
    "Workflow",
    "WorkflowSummary",
    "__version__",
    "new_idempotency_key",
]
