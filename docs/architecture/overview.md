# Architecture Overview

FlowForge is a distributed workflow orchestration engine built on a modular monolith architecture.

## High-Level Components

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Client    │────▶│  REST API   │────▶│   Django    │
│  (SDK/CLI)  │     │   (DRF)     │     │   Models    │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
                       ┌────────────────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   PostgreSQL    │
              │  (State Store)  │
              └─────────────────┘
                       ▲
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│  Scheduler  │ │   Worker    │ │   Worker    │
│  (Process)  │ │  (Process)  │ │  (Process)  │
└──────┬──────┘ └──────┬──────┘ └──────┬──────┘
       │               │               │
       └───────────────┼───────────────┘
                       │
                       ▼
              ┌─────────────────┐
              │    RabbitMQ     │
              │  (Task Queue)   │
              └─────────────────┘
```

## Runtime Processes

- **Web:** Django ASGI/WSGI serving REST API requests.
- **Scheduler:** Monitors PostgreSQL for eligible tasks and dispatches them to RabbitMQ.
- **Worker:** Consumes tasks from RabbitMQ, executes them, and writes results back to PostgreSQL.

## Design Principles

1. **State authority:** PostgreSQL is the single source of truth.
2. **At-least-once delivery:** RabbitMQ guarantees message delivery; workers must be idempotent.
3. **Transactional outbox:** Scheduler publishes tasks via an outbox pattern to avoid dual-write issues.
4. **Lease-based execution:** Workers acquire time-bounded leases on tasks to prevent duplicate execution.

## Technology Stack

| Layer        | Technology        |
|--------------|-------------------|
| API          | Django REST Framework |
| ORM          | Django ORM        |
| Database     | PostgreSQL 16     |
| Cache        | Redis 7           |
| Broker       | RabbitMQ 3.13     |
| Tasks        | Celery 5.4 (Phase 0 infra; custom scheduler planned for Phase 4) |
| Logging      | structlog         |
| Containers   | Docker / Kubernetes |
