"""A fake transport so the client's behaviour can be tested without a server.

The client's interesting logic — retry, pagination, error mapping, polling — is
all in how it reacts to responses. Answering from a table tests that directly,
deterministically, and without a socket.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from flowforge_client._transport import Response

#: A response rule: either a fixed Response or a callable that receives the
#: request and returns one.
Rule = Response | Callable[[str, str, dict[str, str], bytes | None], Response]


def json_response(status: int, body: Any, **headers: str) -> Response:
    """A Response with a JSON body and the headers a real server would send."""
    merged = {"content-type": "application/json"}
    merged.update({key.lower().replace("_", "-"): value for key, value in headers.items()})
    return Response(status=status, body=body, headers=merged)


class FakeTransport:
    """Answers requests from a queue of rules, recording every request made.

    Attributes:
        requests: Every ``(method, url, headers, body)`` the client sent, in
            order. Tests assert on this to prove *how* a call was made — a
            retry, an idempotency key, a query parameter.
        calls: How many requests were attempted.

    """

    def __init__(self, *rules: Rule) -> None:
        """Stage the responses, in the order they will be asked for."""
        self._rules = list(rules)
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []

    @property
    def calls(self) -> int:
        """How many requests were attempted."""
        return len(self.requests)

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Response:
        """Answer one request from the next rule.

        Raises:
            AssertionError: if the test made more requests than it staged. That
                is a test bug, and failing loudly is better than returning a
                confusing response.

        """
        self.requests.append((method, url, dict(headers), body))
        if not self._rules:
            raise AssertionError(
                f"unexpected request {method} {url}; no rule left ({len(self.requests)} sent)"
            )
        rule = self._rules.pop(0)
        if callable(rule):
            return rule(method, url, headers, body)
        return rule

    # -- assertions ---------------------------------------------------------

    def last_body(self) -> Any:
        """The JSON body of the last request, decoded."""
        body = self.requests[-1][3]
        return json.loads(body) if body else None

    def last_headers(self) -> dict[str, str]:
        """The headers of the last request."""
        return self.requests[-1][2]
