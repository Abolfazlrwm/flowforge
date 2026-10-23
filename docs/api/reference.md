# API Reference

Every endpoint, with the request it takes and the response it returns. The
authoritative machine-readable form is
[`docs/api/openapi.yaml`](openapi.yaml), which CI regenerates and compares; this
page is the human-readable view of the same surface.

All paths are relative to `/api/v1/`. All bodies are JSON. All timestamps are ISO
8601 UTC.

## Common shapes

### Error envelope

Every failure returns the same shape, whatever the cause:

```json
{
  "error": {
    "code": "not_found",
    "message": "No run matches the given query.",
    "details": {}
  }
}
```

`code` is stable and safe to branch on; `message` is for a human and may change.

| `code` | Status | Meaning |
| --- | --- | --- |
| `validation_error` | 400 / 422 | The body is not a valid definition or trigger |
| `not_authenticated` | 401 | No credential, or it is invalid/expired |
| `permission_denied` | 403 | Authenticated, but not allowed |
| `not_found` | 404 | Does not exist **in this tenant** |
| `definition_conflict` | 409 | That name and version exists with different content |
| `throttled` | 429 | A rate limit was exceeded; see `Retry-After` |
| `internal_error` | 500 | The server failed |

### Pagination

List endpoints use DRF cursor pagination:

```json
{"next": "http://host/api/v1/runs/?cursor=cD0y", "previous": null, "results": [...]}
```

`next` is `null` on the last page. Follow the URL as given; do not rebuild it.

## Definitions

### `GET /workflows/`

Published definitions, without their tasks. Paginated.

```json
{"id": "…", "name": "etl", "version": 1, "description": "…",
 "created_at": "2026-10-23T10:00:00Z", "task_count": 3}
```

### `POST /workflows/`

Publish a definition. Idempotent by `(name, version)`.

```json
{
  "name": "etl",
  "version": 1,
  "description": "Extract and load.",
  "tasks": [
    {"key": "extract", "task_type": "echo", "config": {"value": "raw"}},
    {"key": "load", "task_type": "noop", "depends_on": ["extract"]}
  ]
}
```

| Response | When |
| --- | --- |
| `201` | Created |
| `200` | Already published with **identical** content |
| `409` | Already published with **different** content — bump the version |
| `400` / `422` | Not a valid definition (duplicate key, dangling dependency, cycle, bad config) |

`task_type` must name a registered handler (`noop`, `echo`, `add`,
`wait_for_file`, `fail`). A dispatch never imports code.

### `GET /workflows/{name}/{version}/`

One definition with its tasks.

```json
{"id": "…", "name": "etl", "version": 1, "description": "…",
 "created_at": "…",
 "tasks": [{"key": "extract", "task_type": "echo", "depends_on": [], "config": {}}]}
```

`config` is **redacted**: a task may carry a credential to execute, and a reader
does not receive it. The worker still gets the real config in its dispatch.

## Runs

### `POST /runs/`

Trigger a run.

```json
{"name": "etl", "version": 1}
```

Optional header `Idempotency-Key: <string>`. The same key with the same body
returns the original run with `Idempotent-Replay: true`; the same key with a
different body is `422`.

`202` — accepted, not finished:

```json
{
  "id": "…", "workflow_name": "etl", "workflow_version": 1,
  "state": "pending", "created_at": "…", "started_at": null, "finished_at": null,
  "task_runs": [
    {"id": "…", "task_key": "extract", "state": "ready", "attempt": 1,
     "fencing_token": 0, "lease_owner": null, "lease_expires_at": null,
     "created_at": "…", "started_at": null, "finished_at": null}
  ]
}
```

A task with no dependencies starts `ready`; one with dependencies starts
`pending`. The run itself starts `pending` and becomes `running` when the
scheduler first advances it.

| Failure | When |
| --- | --- |
| `404` | No such definition in this tenant |
| `422` | The idempotency key was reused with a different body |
| `429` | The `trigger` scope's rate limit |

### `GET /runs/`

Runs visible to the caller, without task runs. Paginated. Each row carries
`id`, `workflow_name`, `workflow_version`, `state`, `created_at`, `started_at`,
`finished_at`.

### `GET /runs/{id}/`

One run with its task runs — the shape `POST /runs/` returns.

### `GET /runs/{id}/events/`

The append-only event log of the run's tasks. Not paginated: the row count is
bounded by the task count times the transitions each task can make.

```json
{"results": [
  {"id": "…", "task_key": "extract", "kind": "promote",
   "from_state": "pending", "to_state": "ready", "created_at": "…"}
]}
```

`kind` is one of `promote`, `skip`, `retry`, `dead`, `transition`.

## Task runs

### `POST /task-runs/{id}/requeue/`

Move a `failed` or `dead` task run back to `ready` so it runs again.

```json
{"id": "…", "task_key": "extract", "state": "ready", "attempt": 2, "fencing_token": 1}
```

| Failure | When |
| --- | --- |
| `409` | The task is not in a requeueable state — requeueing a running task would execute it twice |
| `404` | No such task run in this tenant |

## Run states

```
WorkflowRun:  pending -> running -> succeeded | failed | cancelled
TaskRun:      pending -> ready -> running -> succeeded | failed | retried | dead
                              -> skipped
                              -> cancelled
```

A run is `succeeded` when every task is terminal and at least one succeeded. A
`dead` task means every attempt was used up and a human decision is needed; it
does not by itself fail the run.

## Members

### `GET /members/`

Members of the caller's organization. Requires the `admin` role or above,
because the response exposes usernames and e-mail addresses.

```json
{"id": "…", "username": "alice", "email": "alice@example.com", "role": "member"}
```

### `POST /members/`

```json
{"username": "bob", "role": "member"}
```

`201` on success. `409` if the user is already a member.

### `PATCH /members/{id}/`

```json
{"role": "admin"}
```

### `DELETE /members/{id}/`

`204`. The **last owner cannot be removed or demoted** — `409`, because an
organization with no owner cannot be administered. An admin cannot modify an
owner, or grant the owner role.

## API keys

### `GET /api-keys/`

Key metadata only: `id`, `name`, `prefix`, `scopes`, `created_at`,
`last_used_at`, `expires_at`, `revoked_at`. The secret is never returned.

### `POST /api-keys/`

```json
{"name": "ci", "scopes": ["read", "write"], "expires_at": "2027-01-01T00:00:00Z"}
```

`201` returns the plaintext key **once**:

```json
{"id": "…", "name": "ci", "key": "ff_1a2b3c4d_…", "prefix": "1a2b3c4d", …}
```

Store it now; it cannot be retrieved again.

### `POST /api-keys/{id}/rotate/`

Returns a new plaintext key and invalidates the old one.

### `POST /api-keys/{id}/revoke/`

`204`. The key stops authenticating immediately.

## Observability

### `GET /metrics/`

Prometheus text exposition format. **Unauthenticated** and aggregate only — no
tenant identifier, username or task key — so a scrape needs no credential. Bind
it to a private network.

### `GET /schema/` and `GET /docs/`

The OpenAPI document and a Swagger UI over it. Serving them is for humans
reading the docs site; the committed file is the reviewed artifact.

## Health probes

Outside `/api/v1/`, mounted at the site root:

| Path | Purpose |
| --- | --- |
| `/health/live/` | The process is up. No dependency is checked. |
| `/health/ready/` | Every dependency, named: `{"status": "ready", "postgres": "ok", "redis": "ok", "rabbitmq": "ok"}`. `503` when one is down, and the body says which. |

## Rate limits

Per caller, per scope:

| Scope | Default |
| --- | --- |
| `auth` | 10/min |
| `trigger` | 60/min |
| `read` | 300/min |

`429` carries `Retry-After`. The rates are configurable through
`THROTTLE_AUTH_RATE`, `THROTTLE_TRIGGER_RATE` and `THROTTLE_READ_RATE`.
