# Build stage
FROM python:3.13-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY manage.py ./
COPY taskforge/ ./taskforge/
COPY core/ ./core/
COPY api/ ./api/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[dev]"

# Production stage
FROM python:3.13-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=taskforge.settings

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --system taskforge && \
    useradd --system --gid taskforge --create-home --home-dir /app taskforge

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

RUN python manage.py collectstatic --noinput && \
    chown -R taskforge:taskforge /app

USER taskforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live/ || exit 1

CMD ["gunicorn", "taskforge.wsgi:application", "-b", "0.0.0.0:8000", "-w", "4"]
