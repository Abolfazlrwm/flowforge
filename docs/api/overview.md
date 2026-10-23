# API Overview

The HTTP surface over the workflow definition engine. A view picks a queryset,
declares permissions and throttles, calls one service, and serialises the
result; the rules themselves live in `core/`, not here.

Everything is under `/api/v1/`. The version is in the path rather than a header
so it is visible in a log line, a browser history and a bug report. When v2
exists it gets a new module and a new prefix, and v1 keeps working until it is
explicitly retired.

## Authentication

Two mechanisms, because they fail differently:

| Mechanism | Header | Revocable | Use when |
| --- | --- | --- | --- |
| DRF token | `Authorization: Token <key>` | Yes — delete the row | You need deactivation or revocation to take effect immediately |
| JWT | `Authorization: Bearer <access>` | No — it expires | You want no per-request database lookup |

```bash
# A DRF token
curl -sX POST https://example.com/api/v1/auth/token/ \
  -d 'username=alice&password=...' | jq -r .token

# A JWT pair
curl -sX POST https://example.com/api/v1/auth/jwt/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "alice", "password": "..."}'
```

Both are checked against `is_active` as well as authentication. A deactivated
account still holds a valid token, so checking the flag is what makes
deactivation take effect now rather than at expiry.

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/auth/token/` | POST | Exchange username/password for a DRF token |
| `/auth/jwt/` | POST | Exchange username/password for an access/refresh pair |
| `/auth/jwt/refresh/` | POST | Exchange a refresh token for a new access token |
| `/auth/me/` | GET | The authenticated caller |

## Authorization

Every view names its permission class explicitly, and the default is
authenticated-only, so a view that forgets cannot become public by accident.

| Rule | Applies to |
| --- | --- |
| Authenticated and active | `/auth/me/`, all run endpoints |
| Staff to write, anyone active to read | Definition endpoints |
| Owner or staff (object level) | Reading one run |

A run belonging to somebody else is **403, not 404**: the row exists and the
caller is simply not allowed to read it. Hiding it would be a second,
contradictory policy layered on the first.

## Endpoints

| Endpoint | Method | Success | Failure |
| --- | --- | --- | --- |
| `/workflows/` | GET | 200 | 401 |
| `/workflows/` | POST | 201 created, 200 already published | 400, 401, 403, 409 |
| `/workflows/{name}/{version}/` | GET | 200 | 401, 404 |
| `/runs/` | GET | 200 | 401 |
| `/runs/` | POST | 202 | 400, 401, 404, 422, 429 |
| `/runs/{id}/` | GET | 200 | 401, 403, 404 |
| `/runs/{id}/events/` | GET | 200 | 401, 404 |
| `/task-runs/{id}/requeue/` | POST | 200 | 401, 404, 409 |
| `/members/` | GET, POST | 200, 201 | 401, 403, 409 |
| `/members/{id}/` | PATCH, DELETE | 200, 204 | 401, 403, 404, 409 |
| `/api-keys/` | GET, POST | 200, 201 | 401, 403 |
| `/api-keys/{id}/rotate/` | POST | 200 | 401, 403, 404 |
| `/api-keys/{id}/revoke/` | POST | 204 | 401, 403, 404 |
| `/metrics/` | GET | 200 | — (unauthenticated, aggregate only) |
| `/schema/` | GET | 200 | — |
| `/docs/` | GET | 200 | — |

The request and response body of every one of these is in the
[API reference](reference.md); this page covers the conventions they share.

### Publishing a definition

Staff only. Publishing changes what every future run executes, so it is not an
ordinary authenticated write.

```bash
curl -sX POST https://example.com/api/v1/workflows/ \
  -H "Authorization: Token $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "etl",
        "version": 1,
        "description": "Nightly extract and load",
        "tasks": [
          {"key": "extract", "task_type": "sql"},
          {"key": "load", "task_type": "sql", "depends_on": ["extract"]}
        ]
      }'
```

The body is validated by the JSON Schema in `core.domain.schema`, not by a
serializer — the same schema the parser and the persistence round trip use. A
shape violation is reported with a JSONPath (`$.tasks[1].key`), and a graph
violation (unknown dependency, duplicate key, cycle) names the task. Both are
`400`.

Publishing is **idempotent by content**: reposting the identical definition
returns `200` and the existing row, so a retry after a timeout is not an error.
Posting *different* content under an existing `(name, version)` is `409`; bump
the version instead. `201` versus `200` is decided by the row that was actually
written, not by a pre-check that would race.

### Triggering a run

```bash
curl -sX POST https://example.com/api/v1/runs/ \
  -H "Authorization: Token $TOKEN" \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"name": "etl", "version": 1}'
```

`202 Accepted`, not `201`: the run is accepted, not finished. Each task run is
created in its initial state — `ready` when it has no dependencies, `pending`
otherwise — and nothing claims them yet; that is Phase 4.

The `Idempotency-Key` header is optional. Its absence is not an error; it just
means a retry creates a second run. When present:

- a replay with the same body returns the original run with
  `Idempotent-Replay: true`;
- the same key with a **different** body is `422 idempotency_mismatch`, because
  replaying the original response would silently discard the new request;
- the key is scoped per user, so one user's key cannot suppress another's
  request.

The guarantee comes from a unique index on `(user, key)`, not from a lock: a row
that does not exist yet cannot be locked, so two first-callers would both
proceed. The reservation shares the run's transaction, so a failure to create
the run releases the key rather than leaving a permanent hole that replays as an
empty response.

## Errors

Every failure has the same shape, so a client writes its error handling once:

```json
{
  "error": {
    "code": "validation_error",
    "message": "1 problem(s) in workflow definition:\n  - $.tasks: 'tasks' is a required property",
    "details": {"version": ["This field is required."]}
  }
}
```

`details` is omitted rather than set to `null` when there is nothing to add, so
a client can branch on its presence.

| Code | Status | Meaning |
| --- | --- | --- |
| `validation_error` | 400 | Malformed payload, invalid graph, or an invalid query value |
| `authentication_required` | 401 | No credentials, or unusable credentials |
| `authentication_failed` | 401 | Credentials presented and rejected |
| `permission_denied` | 403 | Authenticated, not permitted |
| `not_found` | 404 | Unknown definition or run |
| `method_not_allowed` | 405 | Method not supported on that resource |
| `conflict` | 409 | Different content under an existing `(name, version)` |
| `idempotency_mismatch` | 422 | Key reused with a different body |
| `throttled` | 429 | Rate limit exceeded |
| `server_error` | 5xx | Unexpected; Django's own 500 path runs rather than a tidy envelope |

An exception the handler does not recognise returns `None`, so Django's normal
500 path runs. Dressing a defect up as a well-formatted client error would hide
it behind a response that looks intentional.

## Pagination

Cursor-based, newest first, with the primary key as a tiebreaker — `created_at`
alone is not unique, and a cursor landing in the middle of a group of equal
values duplicates a row. Offset paging over these append-heavy tables would skip
or repeat rows whenever something is created between two requests.

```json
{"next": "https://example.com/api/v1/runs/?cursor=...", "previous": null, "results": []}
```

`?page_size=` is capped at 100 so a list endpoint cannot be used to exhaust the
database connection's memory.

## Filtering

| Endpoint | Parameters |
| --- | --- |
| `/workflows/` | `name` (exact), `name__startswith`, `version` |
| `/runs/` | `state`, `workflow` (definition name), `created_after`, `created_before` |

An unknown parameter is ignored by the filter backend, which is why the allowed
set is declared in `api/filters.py` rather than read ad hoc from the request.

## Rate limiting

Per-user, per-scope:

| Scope | Rate | Applied to |
| --- | --- | --- |
| `auth` | 10/min | Token and JWT issuance |
| `trigger` | 60/min | Publishing a definition, triggering a run |
| `read` | 300/min | Everything else |

A scope without a configured rate is a `500` on every request, not a disabled
check, so a test asserts that every scope a view declares has a rate.

## Schema

The OpenAPI document is generated by `drf-spectacular`, committed to
`docs/api/openapi.yaml`, and served at `/api/v1/schema/` (Swagger UI at
`/api/v1/docs/`). CI regenerates it and fails on any diff, so the committed file
is the contract: a code change that alters the API cannot pass review without
the schema being regenerated beside it.

```bash
DJANGO_SETTINGS_MODULE=flowforge.settings.test \
  uv run python manage.py spectacular --file docs/api/openapi.yaml
```

Generation is deterministic — verified by generating twice and diffing — which
is what makes the drift check reliable rather than flaky.

## Known limitations

- **Nothing schedules a run.** Triggering creates the task runs in their initial
  states and stops. Claiming and executing them is Phase 4 and Phase 5.
- **Throttle counters are per-process** unless the cache is shared. The default
  cache is process-local, so a multi-process deployment needs Redis before the
  rates above are exact cluster-wide.
- **The schema is checked for drift, not for semantics.** A view that drifts
  from the committed file is caught; a committed file that was always wrong is
  not.
