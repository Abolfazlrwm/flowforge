"""Health check for the processes with no HTTP server (scheduler, publisher, worker).

The Dockerfile's port-8000 check is wrong for them: they run no web server, so it
would always report unhealthy. This verifies what they actually need — the
database and the broker are reachable. If the process itself has exited, Docker
stops and restarts the container; this covers "alive, but its dependencies are
gone", which the port check cannot see at all.
"""

from __future__ import annotations

import pika
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    """Exit 0 only if the database and the broker both answer."""

    help = "Check database and broker connectivity for a non-web process."

    def handle(self, *_args: object, **_options: object) -> None:
        """Check PostgreSQL and RabbitMQ, exiting non-zero if either is down."""
        try:
            connections["default"].cursor().execute("SELECT 1")
        except Exception as exc:
            self.stderr.write(f"database unreachable: {exc}")
            raise SystemExit(1) from exc
        try:
            connection = pika.BlockingConnection(pika.URLParameters(settings.RABBITMQ_URL))
            connection.close()
        except Exception as exc:
            self.stderr.write(f"broker unreachable: {exc}")
            raise SystemExit(1) from exc
        self.stdout.write("ok")
