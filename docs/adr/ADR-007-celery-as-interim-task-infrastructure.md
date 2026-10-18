# ADR-007: Celery as Interim Task Infrastructure (Phase 0)

**Date:** 2026-10-18
**Status:** Accepted — Interim (to be revisited in Phase 4)
**Supersedes:** None

## Context

The TaskForge specification calls for a custom Scheduler with the following
features:

- **Transactional outbox** to avoid dual-write problems between PostgreSQL
  and RabbitMQ.
- **Direct RabbitMQ delivery** using `pika` or `aio-pika`, giving full control
  over channel lifecycle, publisher confirms, and dead-letter exchange
  configuration.
- **Lease-based execution** where workers acquire time-bounded leases on tasks
  with fencing tokens to prevent split-brain execution.
- **Custom scheduler loop** that polls PostgreSQL for eligible tasks and
  publishes them to RabbitMQ.

In Phase 0 (engineering foundation), these components do not yet exist. Celery
was added as infrastructure to provide a working broker/worker stack so that
Docker Compose, CI, and the development environment can be exercised end-to-end.

## Decision

**Keep Celery for Phase 0 and Phase 1.** Write an ADR documenting the
deviation. In Phase 4 (Scheduler), evaluate whether to:

1. **Replace Celery** with a custom scheduler + direct RabbitMQ delivery as
   specified in the original spec, or
2. **Augment Celery** by building the transactional outbox, lease management,
   and fencing tokens on top of Celery's worker model.

This decision will be made at Phase 4 based on:

- Whether Celery's abstraction model conflicts with lease-based execution.
- Whether direct RabbitMQ control is needed for publisher confirms and
  dead-letter routing.
- Whether the fencing token mechanism can be cleanly layered onto Celery
  task headers.

## Rationale

- Celery is the most mature Python task queue and integrates well with Django.
- It provides immediate value in Phase 0 for Docker Compose and CI testing.
- The transactional outbox and lease management are domain concerns that
  can be layered on top of any delivery mechanism — they are not inherently
  incompatible with Celery.
- Removing Celery now would mean shipping Phase 0 with no worker
  infrastructure at all, making the Docker Compose stack incomplete.

## Consequences

- The `scheduler` and `worker` services in `compose.yaml` use Celery beat and
  Celery worker commands.
- The `taskforge/celery.py` module exists and is imported in
  `taskforge/__init__.py`.
- Celery settings (`CELERY_*`) are present in `settings.py`.
- The architecture overview and ROADMAP documents reference Celery as interim
  infrastructure.
- Phase 4 must explicitly decide: keep Celery + layer on custom concerns, or
  rip out Celery and build a custom scheduler.

## Alternatives Considered

- **Remove Celery now:** Would leave Phase 0 without any worker infrastructure.
  RabbitMQ would be in `compose.yaml` but unused. CI would have nothing to
  exercise. Rejected — the infrastructure provides value for testing.
- **Keep Celery but remove from settings and compose:** Would mean Celery is
  installed but not wired. Rejected — provides no value and confuses
  contributors.
