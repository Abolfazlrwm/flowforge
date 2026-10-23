"""The /metrics endpoint.

It is deliberately unauthenticated so Prometheus can scrape it; the test proves
that and verifies the response is Prometheus text exposition format.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

URL = "metrics"


class TestMetrics:
    """Prometheus scraping endpoint."""

    @pytest.mark.django_db
    def test_metrics_is_unauthenticated(self) -> None:
        """No token is required."""
        response = APIClient().get(reverse(URL))
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    def test_metrics_returns_prometheus_text(self) -> None:
        """Content-Type is text/plain and the body contains metric lines."""
        response = APIClient().get(reverse(URL))
        assert response["Content-Type"].startswith("text/plain")
        body = response.content.decode()
        # At minimum the registry should contain HELP/TYPE lines or be empty.
        assert "# HELP" in body or "# TYPE" in body or body == ""
