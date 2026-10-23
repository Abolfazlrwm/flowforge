"""The FlowForge API client.

Every method maps to one documented endpoint, and the resource names match the
API's: ``workflows``, ``runs``, ``task-runs``. Nothing here reaches into Django
or the database — the client talks HTTP, which is why it can be installed in a
service that has nothing to do with the server.

    from flowforge_client import FlowForge

    with FlowForge("http://localhost:8000", token="...") as client:
        run = client.trigger("etl", version=1)
        finished = client.wait(run.id, timeout=120)
        print(finished.state)

The two calls that carry the most design weight:

``trigger``
    A run is created by a POST, which is not idempotent. Pass
    ``idempotency_key`` and a retry — including a retry this client performs
    after a timeout — replays the original run instead of creating a second one.

``wait``
    Polls until the run settles or the deadline passes. It raises
    :class:`TimeoutError` rather than returning a still-running run, because a
    caller that ignores the state would otherwise proceed on a run that has not
    finished. The polling interval grows, so a long run does not cost hundreds of
    requests.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

from ._transport import DEFAULT_TIMEOUT, HttpClient, Transport
from .errors import APIError, ServerError, ValidationError
from .models import (
    TERMINAL_RUN_STATES,
    Event,
    Page,
    Run,
    RunSummary,
    TaskRun,
    Workflow,
    WorkflowSummary,
)

#: Polling starts here and doubles up to the maximum.
INITIAL_POLL_INTERVAL = 0.1
MAX_POLL_INTERVAL = 2.0


class FlowForge:
    """A client for one FlowForge deployment and one credential.

    Args:
        base_url: Root of the API, e.g. ``http://localhost:8000``. The
            ``/api/v1`` prefix is appended automatically, so a caller does not
            have to remember it.
        token: A DRF token, an API key (``ff_...``), or ``None`` for an
            unauthenticated client (only the schema and metrics endpoints work).
        timeout: Per-request timeout in seconds.
        max_attempts: How many times a retryable request is attempted.
        transport: An alternative HTTP implementation; tests use it to answer
            without a socket.
        sleep: Injectable so a test does not wait out a poll interval.

    """

    def __init__(  # noqa: PLR0913 - one keyword per client knob
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = 3,
        transport: Transport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        """Configure the client. See the class docstring for the arguments."""
        self._http = HttpClient(
            base_url=f"{base_url.rstrip('/')}/api/v1",
            token=token,
            transport=transport,
            timeout=timeout,
            max_attempts=max_attempts,
            sleep=sleep,
        )
        self._sleep = sleep

    def __enter__(self) -> FlowForge:
        """Enter a context manager."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Exit a context manager. The client holds no connection to close."""

    # -- authentication -----------------------------------------------------

    @classmethod
    def login(
        cls,
        base_url: str,
        *,
        username: str,
        password: str,
        **kwargs: Any,
    ) -> FlowForge:
        """Exchange a username and password for a token and return a client.

        This is the one call that needs no credential. It is a classmethod
        because a client that is not yet authenticated cannot make it.
        """
        client = cls(base_url, **kwargs)
        response = client._http.request(
            "POST",
            "/auth/token/",
            json_body={"username": username, "password": password},
            idempotent=True,  # a token exchange is safe to repeat
        )
        token = (response.body or {}).get("token")
        if not token:
            raise ValidationError(
                status_code=200,
                code="bad_response",
                message="the token endpoint returned no token",
                response_body=response.body,
            )
        return cls(base_url, token=token, **kwargs)

    # -- workflows ----------------------------------------------------------

    def publish(self, definition: dict[str, Any]) -> Workflow:
        """Publish a workflow definition.

        Publishing is idempotent by ``(name, version)``: reposting identical
        content returns the existing definition, and posting different content
        under an existing version raises :class:`ConflictError` rather than
        silently replacing it.
        """
        response = self._http.request("POST", "/workflows/", json_body=definition, idempotent=True)
        return Workflow.from_payload(response.body)

    def get_workflow(self, name: str, version: int) -> Workflow:
        """Fetch one published definition, with its tasks."""
        response = self._http.request("GET", f"/workflows/{name}/{version}/")
        return Workflow.from_payload(response.body)

    def list_workflows(self, *, page_size: int | None = None) -> Iterator[WorkflowSummary]:
        """Iterate every published definition, following pagination."""
        for payload in self._paginate("/workflows/", page_size=page_size):
            yield WorkflowSummary.from_payload(payload)

    # -- runs ---------------------------------------------------------------

    def trigger(
        self,
        name: str,
        *,
        version: int = 1,
        idempotency_key: str | None = None,
    ) -> Run:
        """Start a run of a published definition.

        Args:
            name: The workflow name.
            version: The workflow version.
            idempotency_key: A caller-chosen key. Two triggers with the same key
                and the same body produce one run; the second returns the first.
                **Pass one** if a retry is possible — including this client's own
                retry after a timeout, which otherwise starts a second run.

        Returns:
            The run, with every task run in its initial state.

        """
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        response = self._http.request(
            "POST",
            "/runs/",
            json_body={"name": name, "version": version},
            headers=headers,
            # A POST without a key is not safe to retry: it would start a second
            # run. With a key it is, because the server replays.
            idempotent=idempotency_key is not None,
        )
        return Run.from_payload(response.body)

    def get_run(self, run_id: str) -> Run:
        """Fetch one run, with the state of each of its tasks."""
        response = self._http.request("GET", f"/runs/{run_id}/")
        return Run.from_payload(response.body)

    def list_runs(self, *, page_size: int | None = None) -> Iterator[RunSummary]:
        """Iterate every run visible to this credential, following pagination."""
        for payload in self._paginate("/runs/", page_size=page_size):
            yield RunSummary.from_payload(payload)

    def run_events(self, run_id: str) -> list[Event]:
        """Return the append-only event log of a run's tasks.

        The endpoint is not paginated: a run's event count is bounded by its task
        count times the number of transitions each task can make.
        """
        response = self._http.request("GET", f"/runs/{run_id}/events/")
        payload = response.body or {}
        rows = payload.get("results", payload) if isinstance(payload, dict) else payload
        return [Event.from_payload(row) for row in rows or ()]

    def wait(
        self,
        run_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = INITIAL_POLL_INTERVAL,
        max_poll_interval: float = MAX_POLL_INTERVAL,
    ) -> Run:
        """Poll a run until it settles, and return it.

        Args:
            run_id: The run to follow.
            timeout: Seconds to wait before giving up.
            poll_interval: The first interval between polls.
            max_poll_interval: The ceiling the interval doubles up to, so a long
                run costs a handful of requests rather than hundreds.

        Returns:
            The settled run. It may have failed — check ``run.succeeded``.

        Raises:
            TimeoutError: the run did not settle within ``timeout``. The last
                observed state is in the message, because "still running" and
                "stuck in ready" are different problems.

        """
        deadline = time.monotonic() + timeout
        interval = poll_interval
        run = self.get_run(run_id)
        while not run.is_terminal:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"run {run_id} did not settle within {timeout}s (last state: {run.state})"
                )
            self._sleep(interval)
            interval = min(interval * 2, max_poll_interval)
            run = self.get_run(run_id)
        return run

    def wait_for_success(self, run_id: str, *, timeout: float = 300.0) -> Run:
        """Like :meth:`wait`, but raise if the run did not succeed.

        Raises:
            FlowForgeError: the run settled in a state other than ``succeeded``.
                The run is attached as ``.run`` so a caller can report which task
                failed.

        """
        run = self.wait(run_id, timeout=timeout)
        if not run.succeeded:
            error = APIError(
                status_code=200,
                code=f"run_{run.state}",
                message=f"run {run.id} ended {run.state}",
                response_body=run,
            )
            error.run = run  # type: ignore[attr-defined]
            raise error
        return run

    # -- task runs ----------------------------------------------------------

    def requeue_task_run(self, task_run_id: str) -> TaskRun:
        """Move a failed or dead task run back to READY so it runs again.

        Only a ``failed`` or ``dead`` task can be requeued; anything else is a
        conflict, because requeueing a running task would run it twice.
        """
        response = self._http.request("POST", f"/task-runs/{task_run_id}/requeue/")
        return TaskRun.from_payload(response.body)

    # -- helpers ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return the readiness payload, which names every dependency and its state.

        A readiness probe answers ``503`` when a dependency is down, and the body
        is the useful part — it says *which* dependency. So a 503 is caught and
        its payload returned rather than raised: the caller reads
        ``payload["status"]``.

        A single attempt: "is it ready now" is a point-in-time question, and a
        retried 503 would answer a different one. A caller that wants to wait for
        readiness polls this itself.
        """
        # Not under /api/v1: the probes are mounted at the site root.
        base = self._http.base_url.rsplit("/api/v1", 1)[0]
        try:
            response = self._http.request("GET", "", raw_url=f"{base}/health/ready/", attempts=1)
        except ServerError as exc:
            return dict(exc.response_body or {})
        return dict(response.body or {})

    def _paginate(self, path: str, *, page_size: int | None) -> Iterator[dict[str, Any]]:
        """Yield every row of a cursor-paginated endpoint."""
        params = {"page_size": page_size} if page_size else None
        response = self._http.request("GET", path, params=params)
        page = self._as_page(response.body)
        for row in page.results:
            yield row
        while page.next_url:
            response = self._http.request("GET", "", raw_url=page.next_url)
            page = self._as_page(response.body)
            for row in page.results:
                yield row

    @staticmethod
    def _as_page(body: Any) -> Page:
        """Normalise a paginated body into a :class:`Page`."""
        if not isinstance(body, dict):
            return Page(results=tuple(body or ()))
        return Page(
            results=tuple(body.get("results") or ()),
            next_url=body.get("next"),
            previous_url=body.get("previous"),
        )


def new_idempotency_key() -> str:
    """Return a fresh key for :meth:`FlowForge.trigger`.

    A helper rather than a default argument: a default would be evaluated once,
    at import time, so every trigger in the process would share one key and
    collapse into a single run.
    """
    return uuid.uuid4().hex


__all__ = [
    "TERMINAL_RUN_STATES",
    "FlowForge",
    "new_idempotency_key",
]
