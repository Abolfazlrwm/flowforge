# Python SDK

`flowforge-client` is a dependency-free client for the REST API. It is a separate
distribution from the server, so installing it cannot constrain your
application's dependency set.

```bash
pip install flowforge-client
```

```python
from flowforge_client import FlowForge, new_idempotency_key

with FlowForge("http://localhost:8000", token="ff_...") as client:
    run = client.trigger("etl", version=1, idempotency_key=new_idempotency_key())
    finished = client.wait(run.id, timeout=120)
    print(finished.state)
```

## Design

| Decision | Why |
| --- | --- |
| **No runtime dependencies** | A client is installed beside an application whose resolution it must not change. `urllib.request` is enough for JSON over HTTP. |
| **Frozen dataclasses, not dicts** | `run.task_runs[0].state` gets an editor completion and an `AttributeError` on a typo, not a `KeyError` three layers down. |
| **Typed errors from the envelope** | The API's `error.code` is stable, so the client maps it to a class once and you `except NotFoundError`. |
| **`wait` raises on timeout** | A caller that ignored a still-running run would proceed on unfinished work. |
| **`trigger` retries only with a key** | A POST without one would start a second run. |

## Constructing a client

```python
FlowForge(base_url, *, token=None, timeout=30.0, max_attempts=3)
FlowForge.login(base_url, *, username=..., password=...)   # returns a client
```

`base_url` is the deployment root (`http://localhost:8000`); the `/api/v1` prefix
is appended for you. `token` accepts a DRF token or an `ff_...` API key. The
client is a context manager, though it holds no connection to close.

## Workflows

```python
workflow = client.publish(definition)  # idempotent by (name, version)
workflow = client.get_workflow("etl", 1)  # with its tasks
for summary in client.list_workflows():  # follows pagination
    print(summary.name, summary.version, summary.task_count)
```

`publish` returns the existing definition when the content is identical and
raises `ConflictError` when it differs — publishing never silently replaces.

## Runs

```python
run = client.trigger("etl", version=1, idempotency_key=new_idempotency_key())
run = client.get_run(run_id)
for summary in client.list_runs():
    print(summary.id, summary.state)
events = client.run_events(run_id)
```

### Waiting

```python
run = client.wait(run_id, timeout=300)  # returns a settled run, failed or not
run = client.wait_for_success(run_id)  # raises if it did not succeed
```

`wait` polls with a growing interval (100 ms doubling to 2 s), so a long run
costs a handful of requests rather than hundreds. It raises `TimeoutError`
naming the last observed state, because "still running" and "stuck in ready" are
different problems.

A run that ended `failed` is returned normally — the state is the answer. Use
`wait_for_success` when a non-success state is an error for your program.

### Idempotency

```python
key = new_idempotency_key()  # once, before the retry window
run = client.trigger("etl", idempotency_key=key)
```

Two triggers with the same key and body produce one run; the second returns the
first. This is what makes a retry safe — including the client's own retry after a
timeout, which the client only performs when a key is present.

Do **not** use a module-level constant as the key: every trigger in the process
would collapse into a single run.

## Task runs

```python
task = client.requeue_task_run(task_run_id)  # failed/dead -> ready
```

Requeueing a task that is not `failed` or `dead` raises `ConflictError`, because
requeueing a running task would execute it twice.

## Errors

```
FlowForgeError
├── TransportError        no HTTP response (connection, timeout, malformed body)
└── APIError              the server answered with an error status
    ├── BadRequestError           400
    ├── AuthenticationError       401
    ├── ForbiddenError            403
    ├── NotFoundError             404
    ├── ConflictError             409
    ├── ValidationError           422
    ├── ThrottledError            429  (has .retry_after)
    └── ServerError               5xx
```

Every `APIError` carries `status_code`, `code`, `message`, `details` and
`response_body`. A 5xx never becomes a 4xx type even when the body claims a 4xx
code: the status is the more reliable signal about whether a retry is safe.

```python
from flowforge_client import ConflictError, NotFoundError

try:
    client.publish(definition)
except ConflictError as exc:
    print(f"{exc.code}: {exc.message}")  # definition_conflict: ...
except NotFoundError:
    print("no such definition in this tenant")
```

`NotFoundError` for another tenant's id is deliberate: the row is invisible, not
merely forbidden.

## Retries

| Status | Retried |
| --- | --- |
| 429, 500, 502, 503, 504 | Yes, with exponential backoff and jitter |
| Any other 4xx | No — it will fail the same way |

`Retry-After` is honoured when the server sends it. Backoff is jittered so that
clients which failed together do not retry together and reproduce the overload.

A non-idempotent request is retried **only** when it carries an
`Idempotency-Key`.

## Health

```python
client.health()  # {"status": "ready", "postgres": "ok", "redis": "ok", "rabbitmq": "ok"}
```

A single attempt: "is it ready now" is a point-in-time question. A `503` is
returned rather than raised, because the body names the dependency that is down.

## Testing your integration

The client accepts a `transport`, so you can answer requests from a table instead
of running a server:

```python
from flowforge_client import FlowForge
from flowforge_client._transport import Response


class FakeTransport:
    def send(self, method, url, *, headers, body, timeout):
        return Response(
            status=202,
            body={
                "id": "1",
                "workflow_name": "etl",
                "workflow_version": 1,
                "state": "pending",
                "task_runs": [],
            },
            headers={},
        )


client = FlowForge("http://test", token="t", transport=FakeTransport())
```

This is how the client's own 52 tests run — see
[`sdk/tests/`](https://github.com/Abolfazlrwm/flowforge/tree/main/sdk/tests).

## What the SDK does not do

- **No async client.** The API is request/response and the interesting latency is
  the pipeline's, not the HTTP call's; an async variant would add a second
  implementation to keep in step for no measured benefit.
- **No automatic retry of a trigger without a key.** That is a deliberate refusal,
  not an omission.
- **No local workflow validation.** The server owns the schema. Validating twice
  would mean two sources of truth that can disagree.

## See also

- [CLI](../cli.md) — the same operations from a shell
- [API reference](../api/reference.md) — the endpoints behind every method
- [Examples](https://github.com/Abolfazlrwm/flowforge/tree/main/examples)
