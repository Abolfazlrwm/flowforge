.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test check migrate run demo compose-up compose-down clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies from the lock file
	uv sync --locked --extra dev

lint:  ## Lint with ruff
	uv run ruff check .

format:  ## Format with ruff
	uv run ruff format .

typecheck:  ## Type-check with mypy (strict)
	uv run mypy flowforge core api --ignore-missing-imports

test:  ## Run unit tests with coverage
	uv run pytest tests/unit -v --cov

check: lint typecheck test  ## Run every check CI runs (except Docker)

migrate:  ## Apply database migrations
	uv run python manage.py migrate

run:  ## Start the development server
	uv run python manage.py runserver

compose-up:  ## Build and start the full stack in the background
	docker compose up --build -d

compose-down:  ## Stop the stack and delete its volumes
	docker compose down -v

demo:  ## Start the stack and assert both health probes
	docker compose up --build -d
	@echo "Waiting for readiness..."
	@for i in $$(seq 1 90); do \
		if curl -fsS http://localhost:8000/health/ready/ > /tmp/flowforge-ready.json 2>/dev/null; then \
			echo "ready: $$(cat /tmp/flowforge-ready.json)"; \
			echo "live:  $$(curl -fsS http://localhost:8000/health/live/)"; \
			exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "stack did not become ready"; \
	docker compose logs --no-color --tail=50; \
	exit 1

clean:  ## Remove local caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml build dist
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
