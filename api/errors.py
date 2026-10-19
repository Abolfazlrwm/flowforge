"""The error envelope every failure is rendered with.

One shape for every failure, so a client writes its error handling once:

```json
{"error": {"code": "validation_error", "message": "...", "details": [...]}}
```

Three things this module is responsible for:

1. **A stable `code`.** Status codes are not enough: a client needs to tell a
   stale idempotency key from a validation failure, and both are 400/409 in some
   designs. The code is the contract; the message is for a human.
2. **Mapping domain errors to HTTP in one place.** Views must not catch domain
   exceptions and decide on a status code — that is business logic leaking into
   the transport layer, and it drifts the moment a second view exists. The
   handler below owns the whole mapping.
3. **Not swallowing unknown errors.** Anything DRF does not recognise returns
   ``None`` so Django's own 500 path runs. Rendering a stack trace as a tidy JSON
   envelope would hide a defect behind a well-formatted response.
"""

from __future__ import annotations

from contextlib import suppress
from enum import StrEnum
from typing import Any

from core.domain.errors import DefinitionError
from core.domain.schema import DefinitionSchemaError
from core.repositories.definitions import DefinitionConflictError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class ErrorCode(StrEnum):
    """Machine-readable reason for a failure.

    Values are part of the public API contract. Renaming one is a breaking
    change and needs a new API version, not a new string.
    """

    VALIDATION_ERROR = "validation_error"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    CONFLICT = "conflict"
    THROTTLED = "throttled"
    SERVER_ERROR = "server_error"


#: Fallback code and human message per status, used when DRF gives no `detail`.
#: Ordered by status so a reader can check it against the HTTP spec at a glance.
_BY_STATUS: dict[int, tuple[ErrorCode, str]] = {
    status.HTTP_400_BAD_REQUEST: (ErrorCode.VALIDATION_ERROR, "The request is invalid."),
    status.HTTP_401_UNAUTHORIZED: (
        ErrorCode.AUTHENTICATION_REQUIRED,
        "Authentication credentials were not provided or are invalid.",
    ),
    status.HTTP_403_FORBIDDEN: (
        ErrorCode.PERMISSION_DENIED,
        "You do not have permission to perform this action.",
    ),
    status.HTTP_404_NOT_FOUND: (ErrorCode.NOT_FOUND, "The requested resource was not found."),
    status.HTTP_405_METHOD_NOT_ALLOWED: (
        ErrorCode.METHOD_NOT_ALLOWED,
        "That method is not allowed on this resource.",
    ),
    status.HTTP_409_CONFLICT: (ErrorCode.CONFLICT, "The request conflicts with existing state."),
    status.HTTP_429_TOO_MANY_REQUESTS: (
        ErrorCode.THROTTLED,
        "Request was throttled. Retry later.",
    ),
}


def error_body(
    code: ErrorCode,
    message: str,
    details: Any | None = None,
) -> dict[str, Any]:
    """Build the envelope.

    ``details`` is omitted rather than set to null when there is nothing to add,
    so a client can branch on its presence instead of on its emptiness.
    """
    error: dict[str, Any] = {"code": code.value, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


class Conflict(APIException):
    """409, for a request that is well-formed but clashes with existing state.

    DRF ships 400/401/403/404/405/406/415/429 but not 409, so this is the one
    status that needs a class of its own.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with existing state."
    default_code = ErrorCode.CONFLICT.value


def _normalise(data: Any) -> tuple[str, Any | None]:
    """Split DRF's response body into a message and optional details.

    DRF produces two shapes: ``{"detail": "..."}`` for a single failure, and a
    mapping of ``field -> [messages]`` for validation. Collapsing both into the
    same envelope is the point of this module, so the split happens once here
    rather than in every client.
    """
    if isinstance(data, dict) and set(data) == {"detail"}:
        return str(data["detail"]), None
    if isinstance(data, dict) and "non_field_errors" in data and len(data) == 1:
        errors = data["non_field_errors"]
        first = errors[0] if isinstance(errors, list) and errors else errors
        return str(first), None
    if isinstance(data, dict):
        return "One or more fields were invalid.", data
    if isinstance(data, list):
        return "One or more fields were invalid.", data
    return str(data), None


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Render every failure with :func:`error_body`.

    Domain exceptions are mapped here, before DRF is consulted, because a
    repository must not import HTTP status codes and a view must not translate
    them. The order matters: ``DefinitionConflictError`` subclasses
    ``Exception``, not ``DefinitionError``, so it is checked first.
    """
    if isinstance(exc, DefinitionConflictError):
        return Response(
            error_body(ErrorCode.CONFLICT, str(exc)),
            status=status.HTTP_409_CONFLICT,
        )
    if isinstance(exc, (DefinitionError, DefinitionSchemaError)):
        # A malformed or internally inconsistent definition. The message names the
        # task and the reason, which is more useful than a field-level error.
        # `DefinitionSchemaError` is a sibling of `DefinitionError` (both
        # `DomainError`) raised for a shape violation, so it must be caught here
        # too — letting it fall through would 500 a bad payload instead of 400.
        return Response(
            error_body(ErrorCode.VALIDATION_ERROR, str(exc)),
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        # Not a DRF exception. Returning None lets Django's 500 handler run; an
        # unknown error must not be dressed up as a tidy client error.
        return None

    code, fallback = _BY_STATUS.get(
        response.status_code,
        (ErrorCode.SERVER_ERROR, "An unexpected error occurred."),
    )
    if isinstance(exc, APIException) and isinstance(exc.default_code, str):
        # Trust the exception's own code when it has one: `authentication_failed`
        # and `authentication_required` share a status but are not the same
        # problem for a client deciding whether to refresh a token. An unknown
        # code keeps the status-derived default rather than failing the render.
        with suppress(ValueError):
            code = ErrorCode(exc.default_code)

    message, details = _normalise(response.data)
    response.data = error_body(code, message if message != "None" else fallback, details)
    return response
