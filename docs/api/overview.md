# API Overview

!!! warning "Not implemented yet — Phase 3"

    There is no HTTP API. `api/v1/` contains a URL namespace and nothing else.
    This page exists so the documentation nav resolves, and will be replaced with
    the real endpoint reference when Phase 3 lands.

What Phase 3 is committed to, from the acceptance criteria:

| Concern | Requirement |
| --- | --- |
| Versioning | `/api/v1/` — a version in the path, not a header |
| Authentication | Token and JWT, both supported |
| Authorization | Explicit permission classes per view, never a global default that silently allows |
| Pagination | Cursor-based |
| Errors | One envelope shape for every failure |
| Filtering | Query parameters on the list endpoints |
| Idempotency | `Idempotency-Key` on the trigger endpoint, with a concurrency test proving N parallel requests create exactly one run |
| Rate limiting | Per-user throttles |
| Schema | `drf-spectacular` OpenAPI, committed and checked in CI — a stale schema fails the build |

The endpoint list, status codes and payload shapes will be documented here when
they exist, not before.
