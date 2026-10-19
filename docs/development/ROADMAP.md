# Development Roadmap

## Phase 0 — Engineering Foundation (v0.1.0)

- [x] Repository initialization
- [x] Django project setup
- [x] PostgreSQL integration
- [x] Docker Compose
- [x] Environment configuration
- [x] Health checks (liveness + readiness)
- [x] Ruff, mypy, pytest
- [x] pre-commit
- [x] GitHub Actions (lint, test, migration check, Docker build)
- [x] Dependabot
- [x] Initial documentation + MkDocs
- [x] Apache-2.0 license

## Phase 1 — Domain Modeling (v0.2.0)

- [x] Workflow entities
- [x] Task entities
- [x] WorkflowRun
- [x] TaskRun
- [x] State machines
- [x] Domain services
- [x] Persistence models
- [x] State transition tests

## Phase 2 — Workflow Definition Engine (v0.3.0)

- [ ] JSON workflow definitions
- [ ] Schema validation
- [ ] DAG validation
- [ ] Cycle detection
- [ ] Topological sorting
- [ ] Dependency resolution
- [ ] Definition versioning

## Phase 3 — REST API (v0.4.0)

- [ ] Workflow management endpoints
- [ ] Execution endpoints
- [ ] Authentication
- [ ] Authorization
- [ ] Pagination
- [ ] Error handling
- [ ] OpenAPI documentation

## Phase 4 — Scheduler (v0.5.0)

- [ ] Task eligibility logic
- [ ] Scheduler loop
- [ ] Atomic task claiming
- [ ] Transactional outbox
- [ ] RabbitMQ publishing
- [ ] Scheduler recovery
- [ ] **Decision: keep Celery or replace with custom scheduler** (ADR-007)

## Phase 5 — Distributed Workers (v0.6.0)

- [ ] Worker process
- [ ] Task consumption
- [ ] Task execution
- [ ] Heartbeats
- [ ] Lease management
- [ ] Fencing tokens
- [ ] Result persistence
- [ ] Graceful shutdown

## Phase 6 — Reliability (v0.7.0)

- [ ] Retry policies
- [ ] Exponential backoff
- [ ] Idempotency
- [ ] Lease expiration
- [ ] Recovery
- [ ] Dead letter handling
- [ ] Concurrency tests

## Phase 7 — Advanced Workflows (v0.8.0)

- [ ] Parallel execution
- [ ] Conditional branching
- [ ] Scheduling
- [ ] Cancellation
- [ ] Execution history
- [ ] Manual retry
- [ ] Compensation mechanisms

## Phase 8 — Security and Multi-Tenancy (v0.9.0)

- [ ] Organizations
- [ ] Memberships
- [ ] RBAC
- [ ] Tenant isolation
- [ ] API keys
- [ ] Audit logging
- [ ] Security tests

## Phase 9 — Observability and Performance (v0.10.0)

- [ ] Structured logging
- [ ] Metrics
- [ ] Distributed tracing
- [ ] Dashboards
- [ ] Load tests
- [ ] Benchmark reports

## Phase 10 — SDK, CLI and Documentation (v0.11.0)

- [ ] Python SDK
- [ ] CLI
- [ ] Examples
- [ ] Developer documentation
- [ ] API reference

## Phase 11 — Production Hardening (v1.0.0)

- [ ] Security review
- [ ] Migration review
- [ ] Deployment documentation
- [ ] Backup and recovery
- [ ] Kubernetes deployment
- [ ] End-to-end testing
- [ ] Release preparation
