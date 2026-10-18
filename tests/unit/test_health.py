"""Tests for the health check endpoints."""

from django.test import Client
from django.urls import reverse


def test_liveness_returns_ok():
    """Liveness check should return 200 and status alive."""
    client = Client()
    response = client.get(reverse("liveness"))
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert response.json()["service"] == "taskforge"


def test_readiness_returns_degraded_without_dependencies():
    """Readiness check should return 503 when dependencies are unavailable."""
    client = Client()
    response = client.get(reverse("readiness"))
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert "checks" in response.json()
