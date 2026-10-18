# Agent Instructions

This file tracks project context for automated agents and contributors.

## Current Phase

**Phase 0: Engineering Foundation**

## Completed

- Repository initialized with isolated `.git`.
- Django 5.1 project created.
- PostgreSQL, Redis, RabbitMQ integration configured.
- Celery task queue wired.
- Health check endpoint (`/health/`) implemented and tested.
- Code quality tools configured: Ruff, mypy, pytest, pre-commit.
- GitHub Actions CI pipeline for lint/type-check/test.
- Docker Compose local development stack.
- Apache-2.0 license and initial documentation.

## Next Phase

**Phase 1: Domain Modeling**

Planned tasks:
- Workflow entities (Workflow, Task)
- Execution entities (WorkflowRun, TaskRun)
- State machines and transition rules
- Domain services for workflow lifecycle
- Persistence models and migrations
- Comprehensive state transition tests

## Decisions

- Python 3.11+ (using 3.13 for development).
- Django 5.1 LTS track.
- Modular monolith with independent runtime processes.
- `django-environ` for 12-factor configuration.
- `uv` used for fast dependency resolution in local dev.
- Docker daemon was offline during Phase 0 setup; Compose file is ready for when it starts.

## Constraints

- Do not commit `.env` files.
- Do not hardcode secrets.
- Do not create empty packages for aesthetics.
- Every feature must include tests.
- Conventional Commits required.
