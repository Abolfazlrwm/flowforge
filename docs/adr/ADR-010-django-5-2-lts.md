# ADR-010: Move to Django 5.2 LTS

**Date:** 2026-10-19
**Status:** Accepted — Owner decision
**Supersedes:** The Django 5.1 pin established during Phase 0
**Related:** [Issue #11](https://github.com/Abolfazlrwm/flowforge/issues/11)

## Context

Phase 0 pinned `django>=5.1,<5.2`, resolving to Django 5.1.15. That pin was an
explicit owner decision at the time.

While preparing Phase 1 it became apparent that Django 5.1 is no longer
supported upstream. The official support table at
<https://www.djangoproject.com/download/> lists 5.1 under **Unsupported previous
releases**:

| Release series | Latest release | End of mainstream support | End of extended support |
|----------------|----------------|--------------------------|-------------------------|
| 5.1 | 5.1.15 | 2025-04-02 | **2025-12-03** |
| 5.2 LTS | 5.2.18 | 2025-12-03 | **2028-04** |

Because 5.1's extended support ended on 2025-12-03, any security fix published
after that date is not available to this project. 5.1.15 is the final release of
the series.

The consequence is not theoretical. The package metadata advertised
`Framework :: Django :: 5.1`, so the project told users it targets a release line
that receives no fixes.

## Decision

**Move to Django 5.2 LTS.** The constraint becomes `django>=5.2,<5.3` and the
classifier becomes `Framework :: Django :: 5.2`.

This is an **owner decision**, made explicitly on 2026-10-19 in response to
issue #11. It is recorded here so that no later reader has to infer whether the
change was deliberate.

## Rationale

- **5.2 is the current LTS**, supported until April 2028. The next forced
  framework upgrade moves from "already overdue" to roughly eighteen months out.
- **It is the same major line.** There is no rewrite. 5.2 adds
  `CompositePrimaryKey` and changes `Model.save()` behaviour for deferred fields;
  both are relevant to Phase 1 model design and are better absorbed now, before
  the domain models and their migrations exist.
- **Phase 1 is the cheapest moment to change.** The migration history does not
  exist yet. Upgrading after Phase 1 would mean either rewriting that history or
  carrying a squashed initial migration forever.

## Consequences

- `pyproject.toml`: constraint and classifier updated; `uv.lock` regenerated
  (resolves Django 5.2.18).
- Django REST Framework moves to 3.17.2 in the same change, because 3.17 is the
  first series declaring support for Django 5.2. Keeping DRF 3.15 would mean
  running a framework/DRF pair that neither project tests.
- **The stub packages were realigned.** `django-stubs` was resolving to 6.1.2 —
  stubs for Django 6.1 against a Django 5.2 runtime. Stubs newer than the
  runtime let mypy accept APIs that do not exist, which is a silent failure
  rather than a visible one. `django-stubs` is now held at 5.2.x, and
  `djangorestframework-stubs` at 3.16.x because 3.17+ requires django-stubs 6.x.
  Both now sit at or behind the runtime, so drift produces a visible type error
  instead of a green check over broken code.
- **Python stays at 3.13.** Django 5.2 supports Python 3.10–3.13, with 3.14
  added only in 5.2.8. Moving the interpreter is a separate change requiring a
  CI matrix entry and a Docker base-image bump; see "Not decided here".
- `redis-py` stays at 5.x and Celery stays at 5.4. Neither is affected by the
  Django upgrade, and both are constrained by the interim Celery decision
  (ADR-007).
- Phase 1 now writes its models and migrations against a supported framework.

## Alternatives Considered

- **Stay on Django 5.1.** Rejected. It would ship an engine that claims
  correctness guarantees while running an unsupported framework, and every phase
  from 1 to 11 would be built on it.
- **Move to Django 6.0 or 6.1.** Rejected. Neither is an LTS; 6.0's extended
  support ends 2027-04 and 6.1's 2027-12, both sooner than 5.2 LTS. Moving to a
  non-LTS now buys a shorter support window and a larger jump.
- **Move to 5.2 and Python 3.14 in one step.** Deferred, not rejected. See
  below.

## Not decided here

**Python 3.14.** The owner's instruction was to keep 3.13 unless 3.14 is
supported by Django 5.2 *and every dependency*, verified in CI. What is
established:

- Django 5.2 supports 3.14 from 5.2.8 onward; this project resolves 5.2.18.
- The compiled dependencies ship `cp314` wheels: `psycopg-binary` (11),
  `pydantic-core` (31), `rpds-py` (29).
- Every other direct dependency is pure Python, so it has no wheel gate.

What is *not* established is a CI run on 3.14 with the Docker base image
matching. That is the actual bar, and it has not been met, so 3.13 stands. The
Docker ecosystem is configured to ignore Python minor/major bumps until it is.
