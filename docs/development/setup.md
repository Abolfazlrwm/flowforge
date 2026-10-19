# Development Setup

## Prerequisites

- Python >= 3.11
- Docker & Docker Compose (optional, for managed services)
- Git

## Option 1: Local Python Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Copy environment file
cp .env.example .env
# Edit .env to point to your PostgreSQL/Redis/RabbitMQ instances

# Run migrations
python manage.py migrate

# Start development server
python manage.py runserver
```

## Option 2: Docker Compose (Recommended)

```bash
docker compose up --build
```

This starts all services including the database, cache, broker, web server, scheduler, and worker.

## Running Checks

```bash
# Linting
ruff check .
ruff format .

# Type checking
mypy flowforge core api --ignore-missing-imports

# Tests
pytest tests/unit -v
pytest tests/integration -v -m integration
```

## Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```
