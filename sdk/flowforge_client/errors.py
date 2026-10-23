"""The exception hierarchy the client raises.

The API answers every failure with one envelope::

    {"error": {"code": "not_found", "message": "...", "details": {...}}}

so the client parses that envelope once and raises a specific type. A caller can
catch :class:`FlowForgeError` for "anything from FlowForge", :class:`APIError`
for "the server said no", or a subclass for a decision it can act on — a retry
for :class:`ThrottledError`, a fix-the-payload for :class:`ValidationError`.

Transport failures are deliberately *not* API errors: a DNS failure, a refused
connection and a timeout all raise :class:`TransportError`, because none of them
means the server rejected anything.
"""

from __future__ import annotations

from typing import Any


class FlowForgeError(Exception):
    """Base class for every error this client raises."""


class TransportError(FlowForgeError):
    """The request never produced an HTTP response.

    A connection error, a timeout, or a malformed response body. The server may
    or may not have acted on the request — for a trigger, that is what an
    ``Idempotency-Key`` is for.
    """


class APIError(FlowForgeError):
    """The server answered with an error status.

    Attributes:
        status_code: The HTTP status.
        code: The envelope's stable ``error.code``, e.g. ``"not_found"``.
        message: The human-readable message from the envelope.
        details: The envelope's ``error.details``, if any.
        response_body: The raw decoded body, for a code this client does not
            know about. Kept so an unexpected error is still diagnosable.

    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        response_body: Any = None,
    ) -> None:
        """Build the error from an already-parsed envelope."""
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.response_body = response_body
        super().__init__(f"{status_code} {code}: {message}")


class BadRequestError(APIError):
    """400 — the request was malformed in a way the server could not attribute."""


class AuthenticationError(APIError):
    """401 — the token is missing, invalid or expired."""


class ForbiddenError(APIError):
    """403 — the caller is authenticated but not allowed to do this.

    Named ``ForbiddenError`` rather than ``PermissionError`` so it does not
    shadow the builtin, which a caller may legitimately want to catch.
    """


class NotFoundError(APIError):
    """404 — the resource does not exist *in this tenant*.

    Another tenant's id is a 404 rather than a 403 on purpose: the row is
    invisible, not merely forbidden.
    """


class ConflictError(APIError):
    """409 — the resource already exists with different content."""


class ValidationError(APIError):
    """400/422 — the payload is not a valid workflow definition or trigger."""


class ThrottledError(APIError):
    """429 — the caller exceeded a rate limit.

    Attributes:
        retry_after: Seconds to wait, from the ``Retry-After`` header, or
            ``None`` when the server did not say.

    """

    def __init__(self, *, retry_after: float | None = None, **kwargs: Any) -> None:
        """Build the error, carrying the server's ``Retry-After`` when it sent one."""
        self.retry_after = retry_after
        super().__init__(**kwargs)


class ServerError(APIError):
    """5xx — the server failed. Retryable for a read; a trigger may need a key."""


#: Envelope ``error.code`` -> exception type. A code that is not here becomes a
#: plain :class:`APIError`, which still carries everything the envelope had.
_BY_CODE: dict[str, type[APIError]] = {
    "bad_request": BadRequestError,
    "validation_error": ValidationError,
    "not_found": NotFoundError,
    "conflict": ConflictError,
    "definition_conflict": ConflictError,
    "throttled": ThrottledError,
    "authentication_failed": AuthenticationError,
    "not_authenticated": AuthenticationError,
    "permission_denied": ForbiddenError,
}

#: A status at or above this is a server failure, and the status is trusted
#: over the envelope's code when deciding whether a retry is safe.
SERVER_ERROR_THRESHOLD = 500

#: Fallback by status when the envelope is missing or its code is unknown.
_BY_STATUS: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: ThrottledError,
}


def error_from_response(
    *, status_code: int, body: Any, retry_after: float | None = None
) -> APIError:
    """Build the most specific exception the envelope allows.

    A 5xx never becomes a 4xx type even if the body claims a code that maps to
    one: the status is the more reliable signal about whether a retry is safe.
    """
    code = ""
    message = ""
    details: dict[str, Any] | None = None

    if isinstance(body, dict):
        envelope = body.get("error")
        if isinstance(envelope, dict):
            code = str(envelope.get("code") or "")
            message = str(envelope.get("message") or "")
            raw_details = envelope.get("details")
            if isinstance(raw_details, dict):
                details = raw_details

    if not message:
        # A proxy or a misconfigured server can return HTML or an empty body.
        # The status is still worth reporting, so the message says what we know.
        message = f"the server returned {status_code} with no error envelope"

    if status_code >= SERVER_ERROR_THRESHOLD:
        error_type: type[APIError] = ServerError
    else:
        error_type = _BY_CODE.get(code) or _BY_STATUS.get(status_code, APIError)

    kwargs: dict[str, Any] = {
        "status_code": status_code,
        "code": code or f"http_{status_code}",
        "message": message,
        "details": details,
        "response_body": body,
    }
    if error_type is ThrottledError:
        kwargs["retry_after"] = retry_after
    return error_type(**kwargs)
