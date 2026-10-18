# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added
- Initial repository structure and Django project setup.
- PostgreSQL, Redis, and RabbitMQ integration via django-environ.
- Celery configuration for distributed task processing.
- Health check endpoint (`/health/`).
- Ruff, mypy, pytest, and pre-commit configuration.
- GitHub Actions CI pipeline for linting and testing.
- Docker Compose local development stack.
- Apache-2.0 license and initial documentation.
