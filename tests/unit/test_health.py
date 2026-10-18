"""Tests for the health check endpoint."""

from django.test import Client
from django.urls import reverse


def test_health_check_returns_ok():
    """Health check should return 200 and status ok."""
    client = Client()
    response = client.get(reverse("health-check"))
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "flowforge"
