"""Core views for FlowForge."""

import pika
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import HttpRequest, JsonResponse
from django.views import View


class LivenessView(View):
    """Liveness probe — is the process running and responsive?"""

    def get(self, request: HttpRequest) -> JsonResponse:  # noqa: ARG002
        """Handle GET requests."""
        return JsonResponse({"status": "alive", "service": "flowforge"})


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

        # --- RabbitMQ (real AMQP handshake, not just a TCP connect) ---
        # A TCP connect succeeds against anything listening on the port, including
        # a broker that will reject the handshake or a proxy. Opening and closing a
        # real connection proves the broker speaks AMQP and accepts our credentials.
        try:
            params = pika.URLParameters(settings.HEALTH_CHECK_BROKER_URL)
            params.socket_timeout = 3
            connection = pika.BlockingConnection(params)
            connection.close()
            checks["rabbitmq"] = "ok"
        except Exception:
            checks["rabbitmq"] = "error"
            all_ok = False

        status_code = 200 if all_ok else 503
        return JsonResponse(
            {"status": "ready" if all_ok else "degraded", "checks": checks},
            status=status_code,
        )
