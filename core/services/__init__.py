"""Service layer.

The seam between HTTP and the domain. Views call these; these call repositories
and the domain model. A view that imports a model to run a query, or catches a
domain exception to pick a status code, is a bug in the layering.

Definitions are the exception worth naming: `core.repositories.definitions`
already holds their business rules (atomicity, idempotency by fingerprint,
exclusivity by `(name, version)`), so it *is* the service for that aggregate.
Wrapping it in a pass-through module would add a call and no behaviour. Runs need
real orchestration — idempotency, run row, task rows, one transaction — so they
get a module here.
"""

from __future__ import annotations

__all__ = ["runs"]
