# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: resolve dependencies from uv.lock into an isolated virtualenv
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# README.md is referenced by [project].readme, so it must exist before the
# package metadata is generated.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY manage.py ./
COPY flowforge/ ./flowforge/
COPY core/ ./core/
COPY api/ ./api/

# --locked fails the build if uv.lock is out of date.
# --no-editable installs the project into site-packages, so the runtime image
# does not depend on the bind-mounted source tree.
RUN uv sync --locked --no-editable

# ---------------------------------------------------------------------------
# Production
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=flowforge.settings.prod \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system flowforge \
    && useradd --system --gid flowforge --create-home --home-dir /app flowforge

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY . .

# collectstatic runs under production settings so the manifest that
# CompressedManifestStaticFilesStorage expects is generated. Production
# settings refuse to start without a secret key, so an ephemeral one is
# generated for the duration of this build step only.
RUN DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
    DJANGO_ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput \
    && chown -R flowforge:flowforge /app

USER flowforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live/ || exit 1

CMD ["gunicorn", "flowforge.wsgi:application", "-b", "0.0.0.0:8000", "-w", "4"]
