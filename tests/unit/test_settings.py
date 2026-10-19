"""Tests for environment-specific settings and security flags."""

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _setup_django(
    settings_module: str,
    secret_key: str,
    debug: str = "False",
) -> subprocess.CompletedProcess[str]:
    """Run ``django.setup()`` in a clean interpreter with the given settings."""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = settings_module
    env["DJANGO_DEBUG"] = debug
    env["DJANGO_ALLOWED_HOSTS"] = "example.com"
    env["DJANGO_SECRET_KEY"] = secret_key

    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


# ---------------------------------------------------------------------------
# Test environment security posture
# ---------------------------------------------------------------------------


def test_test_settings_run_with_debug_off():
    """The test environment exercises the production code path."""
    assert settings.DEBUG is False


def test_secure_cookies_enabled_when_debug_off():
    """Secure-only cookies are required whenever DEBUG is off."""
    assert settings.SESSION_COOKIE_SECURE is True
    assert settings.CSRF_COOKIE_SECURE is True
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.CSRF_COOKIE_HTTPONLY is True


def test_baseline_hardening_flags():
    """Clickjacking, MIME sniffing and referrer policies are always set."""
    assert settings.X_FRAME_OPTIONS == "DENY"
    assert settings.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert settings.SECURE_REFERRER_POLICY == "same-origin"


# ---------------------------------------------------------------------------
# SECRET_KEY enforcement
# ---------------------------------------------------------------------------


def test_secret_key_required_when_debug_is_off():
    """Base settings must refuse to start without a key when DEBUG is off."""
    result = _setup_django("flowforge.settings.base", secret_key="", debug="False")

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_secret_key_optional_when_debug_is_on():
    """Development is allowed to fall back to a throwaway key."""
    result = _setup_django("flowforge.settings.base", secret_key="", debug="True")

    assert result.returncode == 0, result.stderr


def test_prod_rejects_weak_secret_key():
    """Production must reject short or placeholder secret keys."""
    result = _setup_django("flowforge.settings.prod", secret_key="short")

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_prod_rejects_placeholder_secret_key():
    """The ``change-me`` placeholder from .env.example must not reach prod."""
    result = _setup_django(
        "flowforge.settings.prod",
        secret_key="change-me-in-production-use-a-long-random-string",
    )

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_prod_accepts_strong_secret_key():
    """A long, non-placeholder key is accepted."""
    result = _setup_django("flowforge.settings.prod", secret_key="a" * 64)

    assert result.returncode == 0, result.stderr
