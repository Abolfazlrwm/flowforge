# Architecture Decision Records

Every non-obvious choice in FlowForge is written down with the alternative that
was rejected and the reason. A decision that only exists in a commit message is a
decision the next person will re-litigate.

A short summary of every decision, including the ones without a full ADR, is in
[the decisions index](../development/DECISIONS.md).

| ADR | Decision | Status |
| --- | --- | --- |
| [ADR-007](ADR-007-celery-as-interim-task-infrastructure.md) | Celery as interim task infrastructure | **Superseded** by [ADR-011](ADR-011-custom-scheduler-and-transactional-outbox.md) (owner decision 2026-10-20); Celery removed in Phase 4 |
| [ADR-009](ADR-009-project-name-evaluation.md) | Project name evaluated; "FlowForge" retained | Accepted 2026-10-19 |
| [ADR-010](ADR-010-django-5-2-lts.md) | Move to Django 5.2 LTS | Accepted 2026-10-19 |
| [ADR-011](ADR-011-custom-scheduler-and-transactional-outbox.md) | Custom scheduler and transactional outbox; Celery removed in Phase 4 | **Accepted 2026-10-20 — owner decision** |
| [ADR-012](ADR-012-retry-policy-and-dead-letter-semantics.md) | Retry policy and dead-letter semantics (`DEAD`, consumed attempts, `scheduled_at` backoff) | Accepted 2026-10-21 — scope mandated by the owner's Phase 6 directive |
| [ADR-013](ADR-013-event-log-is-transactional.md) | The event log is transactional with its state change | Accepted 2026-10-22 |
| [ADR-014](ADR-014-multi-tenancy-shared-schema.md) | Multi-tenancy via a shared schema and per-organization scoping | Accepted 2026-10-23 (v0.9.0) |

## How an ADR is written here

1. **Context** — the constraint or problem, stated without the answer.
2. **Decision** — what was chosen, in the present tense.
3. **Consequences** — what becomes easier, what becomes harder, and what is now
   impossible without a new ADR.
4. **Alternatives rejected** — each with the specific reason it lost, not "it was
   worse".

An ADR that records only the winner is a press release, not a record.
