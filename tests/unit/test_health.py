"""Tests for the liveness and readiness probes."""

from unittest.mock import MagicMock, patch

from django.test import Client
from django.urls import reverse


def _fake_connections(postgres_fails: bool = False) -> MagicMock:
    """Build a stand-in for Django's ``connections`` handler."""
    connections = MagicMock()
    if postgres_fails:
        connections.__getitem__.return_value.cursor.side_effect = RuntimeError("db down")
    return connections


def test_liveness_returns_200():
    """Liveness must succeed even when every dependency is down."""
    response = Client().get(reverse("liveness"))

    assert response.status_code == 200
    assert response.json() == {"status": "alive", "service": "flowforge"}


def test_readiness_returns_200_when_all_dependencies_ok():
    """Readiness returns 200 only when PostgreSQL, Redis and RabbitMQ answer."""
    with (
        patch("core.views.connections", _fake_connections()),
        patch("core.views.cache") as fake_cache,
        patch("core.views.socket.create_connection") as fake_socket,
    ):
        response = Client().get(reverse("readiness"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgres": "ok", "redis": "ok", "rabbitmq": "ok"},
    }
    fake_cache.set.assert_called_once()
    fake_socket.assert_called_once()


def test_readiness_returns_503_when_postgres_unavailable():
    """A dead database alone must degrade the probe to 503."""
    with (
        patch("core.views.connections", _fake_connections(postgres_fails=True)),
        patch("core.views.cache"),
        patch("core.views.socket.create_connection"),
    ):
        response = Client().get(reverse("readiness"))

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["postgres"] == "error"
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["rabbitmq"] == "ok"


def test_readiness_returns_503_when_redis_unavailable():
    """A dead cache alone must degrade the probe to 503."""
    with (
        patch("core.views.connections", _fake_connections()),
        patch("core.views.cache") as fake_cache,
        patch("core.views.socket.create_connection"),
    ):
        fake_cache.set.side_effect = RuntimeError("redis down")
        response = Client().get(reverse("readiness"))

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "error"
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["rabbitmq"] == "ok"


def test_readiness_returns_503_when_rabbitmq_unavailable():
    """An unreachable broker alone must degrade the probe to 503."""
    with (
        patch("core.views.connections", _fake_connections()),
        patch("core.views.cache"),
        patch("core.views.socket.create_connection", side_effect=OSError("refused")),
    ):
        response = Client().get(reverse("readiness"))

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["rabbitmq"] == "error"
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["redis"] == "ok"
