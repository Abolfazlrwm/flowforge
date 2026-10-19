# FlowForge

An open-source distributed workflow orchestration engine powered by Django, PostgreSQL, and RabbitMQ.

> **Status:** Phase 0 — Engineering Foundation (v0.1.0-alpha)

## Overview

FlowForge is a production-oriented workflow orchestration platform that enables teams to define, schedule, and execute directed acyclic graph (DAG) workflows across distributed workers. It is designed with correctness, reliability, and observability as first-class concerns.

## Architecture

- **Backend:** Django + Django REST Framework
- **Database:** PostgreSQL (authoritative state)
- **Message Broker:** RabbitMQ (task dispatch)
- **Cache:** Redis (caching + Celery results)
- **Task Queue:** Celery (Phase 0 infrastructure; custom scheduler planned for Phase 4)
- **Observability:** Structured logging with structlog; Prometheus & OpenTelemetry planned
- **Deployment:** Docker Compose (local); Kubernetes (production)

## Quick Start

### Prerequisites

- Python >= 3.11
- Docker & Docker Compose (optional, for services)
- PostgreSQL, Redis, RabbitMQ (or use Docker Compose)

### Local Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/Abolfazlrwm/flowforge.git
   cd flowforge
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

4. Copy environment variables:
   ```bash
   cp .env.example .env
   # Edit .env to match your local services
   ```

5. Run migrations:
   ```bash
   python manage.py migrate
   ```

6. Start the development server:
   ```bash
   python manage.py runserver
   ```

7. Verify the health endpoint:
   ```bash
   curl http://localhost:8000/health/live/
   curl http://localhost:8000/health/ready/
   ```

### Docker Compose

```bash
docker compose up --build
```

This starts PostgreSQL, Redis, RabbitMQ, the web server, scheduler, and worker.

## Development

### Code Quality

```bash
# Linting
ruff check .
ruff format .

# Type checking
mypy flowforge core api --ignore-missing-imports

# Tests
pytest tests/unit -v
```

### Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```

## Testing

```bash
pytest tests/unit -v
pytest tests/integration -v -m integration
```

## Documentation

- [Architecture Overview](docs/architecture/overview.md)
- [Development Setup](docs/development/setup.md)
- [Deployment Guide](docs/deployment/docker.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
