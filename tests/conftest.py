"""Global pytest fixtures and configuration."""

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    """Return a DRF APIClient."""
    return APIClient()
