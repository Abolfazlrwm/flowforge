"""The HTTP layer: one request, with retries, and no third-party dependency.

The client deliberately depends on nothing outside the standard library. A
client library is installed *next to* a user's application, and every dependency
it adds is a version the user's application cannot choose freely — a Django
project pinning ``urllib3`` would be forced by its API client. ``urllib.request``
is enough for JSON over HTTP.

Retries are conservative and explicit: a ``429`` or a ``5xx`` is retried with
exponential backoff and jitter, honouring ``Retry-After`` when the server sends
it; a ``4xx`` is not, because the same request will fail the same way. A
non-idempotent request (a trigger) is only retried when the caller supplied an
``Idempotency-Key``, since without one a retry creates a second run.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import TransportError, error_from_response

#: Statuses worth retrying. 429 and 5xx are transient; everything else is not.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Methods safe to retry without an idempotency key. PUT and DELETE are
#: idempotent by HTTP semantics; POST is not, so it needs the key.
RETRYABLE_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF = 0.25
MAX_BACKOFF = 8.0

#: Below this, a response is a success. Named because ``status < 400`` appears in
#: the retry loop next to ``>= 500`` checks and a bare literal there is easy to
#: misread.
SUCCESS_THRESHOLD = 400


@dataclass(frozen=True, slots=True)
class Response:
    """A decoded HTTP response."""

    status: int
    body: Any
    headers: dict[str, str]


class Transport(Protocol):
    """What the client needs from an HTTP implementation.

    Tests implement this to answer from a table instead of a socket, which is
    what makes the client's behaviour (retry, parsing, pagination) testable
    without a running server.
    """

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response:
        """Perform one request and return the decoded response."""
        ...


class UrllibTransport:
    """The default transport: ``urllib.request`` with JSON in and out."""

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response:
        """Perform one HTTP request.

        Raises:
            TransportError: if no response was produced at all.
            ValueError: if the URL is not ``http`` or ``https``. ``urlopen``
                accepts ``file:`` and ``ftp:`` too, and a client that can be
                pointed at a local path by a caller-supplied base URL is a
                credential-disclosure bug waiting to happen.

        """
        scheme = urllib.parse.urlparse(url).scheme
        if scheme not in ("http", "https"):
            raise ValueError(f"refusing to open a {scheme or 'scheme-less'} URL: {url}")

        # The scheme is checked above, which is what S310 asks for; the noqa is
        # because ruff cannot see across the check.
        request = urllib.request.Request(  # noqa: S310
            url, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as raw:  # noqa: S310
                return Response(
                    status=raw.status,
                    body=_decode(raw.read(), raw.headers.get("Content-Type", "")),
                    headers={key.lower(): value for key, value in raw.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            # An error status is still a response: the body carries the envelope.
            return Response(
                status=exc.code,
                body=_decode(
                    exc.read(), exc.headers.get("Content-Type", "") if exc.headers else ""
                ),
                headers={key.lower(): value for key, value in (exc.headers or {}).items()},
            )
        except urllib.error.URLError as exc:
            raise TransportError(f"{method} {url} failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TransportError(f"{method} {url} timed out after {timeout}s") from exc
        except OSError as exc:
            # A connection reset mid-response surfaces here rather than as
            # URLError. Treat it as transport, not as a server rejection.
            raise TransportError(f"{method} {url} failed: {exc}") from exc


def _decode(raw: bytes, content_type: str) -> Any:
    """Decode a body as JSON, or return text, or ``None`` when empty.

    An empty 204 has no body; a proxy error page is HTML. Neither should raise
    from inside the transport, because the status is the useful part.
    """
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower():
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _retry_after(headers: dict[str, str]) -> float | None:
    """Seconds from a ``Retry-After`` header, when it is a number of seconds.

    The HTTP-date form is legal but rare; this client falls back to its own
    backoff rather than parsing dates, and says so by returning ``None``.
    """
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _backoff(attempt: int, *, jitter: float) -> float:
    """Exponential backoff with jitter, capped.

    Jitter matters when many clients retry together: without it they retry in
    lockstep and reproduce the overload that caused the failure.
    """
    delay: float = min(DEFAULT_BACKOFF * (2 ** (attempt - 1)), MAX_BACKOFF)
    # `random` is right here: jitter only has to be unpredictable to break
    # lockstep, and a cryptographic source would add a syscall per retry for
    # no security benefit.
    jittered: float = delay * (1.0 + random.random() * jitter)  # noqa: S311
    return jittered


class HttpClient:
    """Retries, error mapping and JSON encoding on top of a :class:`Transport`."""

    def __init__(  # noqa: PLR0913 - one keyword per retry knob
        self,
        *,
        base_url: str,
        token: str | None = None,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        jitter: float = 0.5,
        sleep: Any = time.sleep,
    ) -> None:
        """Configure the client.

        Args:
            base_url: Root of the API, including ``/api/v1``.
            token: Credential sent as ``Authorization: Token ...``.
            transport: HTTP implementation; defaults to :class:`UrllibTransport`.
            timeout: Per-request timeout in seconds.
            max_attempts: Attempts for a retryable request.
            jitter: Fraction of the backoff to randomise, so clients that failed
                together do not retry together.
            sleep: Injectable so a test does not wait out a backoff.

        """
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.jitter = jitter
        # Injectable so a test does not actually wait out a backoff.
        self._sleep = sleep

    def request(  # noqa: PLR0913 - one keyword per request component
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool = True,
        raw_url: str | None = None,
        attempts: int | None = None,
    ) -> Response:
        """Send one request, retrying the transient failures.

        Args:
            method: HTTP method.
            path: Path relative to ``base_url``, or an absolute URL when
                ``raw_url`` is given (pagination hands back full URLs).
            json_body: Encoded as JSON when not ``None``.
            params: Query parameters; ``None`` values are dropped.
            headers: Extra headers.
            idempotent: Whether a retry is safe. A trigger is not, unless it
                carries an ``Idempotency-Key``.
            raw_url: A complete URL to use instead of ``base_url + path``.
            attempts: Override the client's attempt count for this call. A
                readiness probe passes 1: "is it ready now" is a point-in-time
                question and retrying it answers a different one.

        Raises:
            TransportError: no response was produced.
            APIError: the server answered with an error status.

        """
        max_attempts = attempts if attempts is not None else self.max_attempts
        url = raw_url or f"{self.base_url}{path}"
        if params:
            query = {key: value for key, value in params.items() if value is not None}
            if query:
                separator = "&" if urllib.parse.urlparse(url).query else "?"
                url = f"{url}{separator}{urllib.parse.urlencode(query)}"

        request_headers = {"Accept": "application/json"}
        if self.token:
            request_headers["Authorization"] = f"Token {self.token}"
        if headers:
            request_headers.update(headers)

        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        # A retry of a non-idempotent request needs a key, or it duplicates work.
        may_retry = idempotent or method.upper() in RETRYABLE_METHODS
        if not idempotent and "Idempotency-Key" in request_headers:
            may_retry = True

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.transport.send(
                    method,
                    url,
                    headers=request_headers,
                    body=body,
                    timeout=self.timeout,
                )
            except TransportError as exc:
                last_error = exc
                if attempt == max_attempts or not may_retry:
                    raise
                self._sleep(_backoff(attempt, jitter=self.jitter))
                continue

            if response.status < SUCCESS_THRESHOLD:
                return response

            if response.status in RETRYABLE_STATUSES and may_retry and attempt < max_attempts:
                wait = _retry_after(response.headers)
                self._sleep(wait if wait is not None else _backoff(attempt, jitter=self.jitter))
                continue

            raise error_from_response(
                status_code=response.status,
                body=response.body,
                retry_after=_retry_after(response.headers),
            )

        # Unreachable in practice: the loop either returns or raises. Kept so a
        # future edit cannot silently swallow the last error.
        raise last_error or TransportError(f"{method} {url} failed")
