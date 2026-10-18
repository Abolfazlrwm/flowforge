"""Core views for TaskForge."""

import socket
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import HttpRequest, JsonResponse
from django.views import View


class LivenessView(View):
    """Liveness probe — is the process running and responsive?"""

    def get(self, request: HttpRequest) -> JsonResponse:  # noqa: ARG002
        """Handle GET requests."""
        return JsonResponse({"status": "alive", "service": "taskforge"})


class ReadinessView(View):
    """Readiness probe — can we serve traffic (DB, Redis, RabbitMQ up)?"""

    def get(self, request: HttpRequest) -> JsonResponse:  # noqa: ARG002
        """Handle GET requests, checking all dependencies."""
        checks: dict[str, str] = {}
        all_ok = True

        # --- PostgreSQL ---
        try:
            connections["default"].cursor().execute("SELECT 1")
            checks["postgres"] = "ok"
        except Exception:
            checks["postgres"] = "error"
            all_ok = False

        # --- Redis ---
        try:
            cache.set("__health_check__", "1", timeout=5)
            cache.delete("__health_check__")
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"
            all_ok = False

        # --- RabbitMQ (TCP connect check on broker host/port) ---
        try:
            broker_url = settings.HEALTH_CHECK_BROKER_URL
            parsed = urlparse(broker_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 5672
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
            checks["rabbitmq"] = "ok"
        except Exception:
            checks["rabbitmq"] = "error"
            all_ok = False

        status_code = 200 if all_ok else 503
        return JsonResponse(
            {"status": "ready" if all_ok else "degraded", "checks": checks},
            status=status_code,
        )
