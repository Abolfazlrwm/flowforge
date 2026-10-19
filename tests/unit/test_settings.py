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


# ---------------------------------------------------------------------------
# Test database selection
# ---------------------------------------------------------------------------

# Runs in a clean interpreter so that the process environment and ``sys.modules``
# start empty and the test settings decide from scratch. ``SIMULATE_DOTENV_MERGE``
# supplies a URL the way a ``.env`` file does: ``flowforge.settings.base`` merges
# the file into ``os.environ`` *while it is being imported*, which is after the
# test settings have already taken their snapshot — so this must run at import
# time, not before the interpreter starts.
_DATABASE_ENGINE_PROBE = """\
import os

if os.environ.get("SIMULATE_DOTENV_MERGE"):
    import environ

    _read_env = environ.Env.read_env

    def _merged(self, *args, **kwargs):
        _read_env(self, *args, **kwargs)
        os.environ.setdefault("DATABASE_URL", os.environ["SIMULATE_DOTENV_MERGE"])

    environ.Env.read_env = _merged

import django

django.setup()
from django.conf import settings

print(settings.DATABASES["default"]["ENGINE"])
"""


def _database_engine(
    env_url: str | None = None,
    dotenv_url: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Import the test settings in a clean interpreter and report the engine.

    ``env_url`` is planted in the *process environment* before the interpreter
    starts, which is the only way a caller can genuinely supply a value. That
    is what CI does.

    ``dotenv_url`` is planted the way a ``.env`` file behaves: it is merged
    into ``os.environ`` by ``read_env`` while ``flowforge.settings.base`` is
    being imported, which is after the test settings have taken their
    snapshot.
    """
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "flowforge.settings.test"
    # Never inherit a caller-supplied DATABASE_URL: the point of these tests is
    # to control exactly where the value comes from.
    env.pop("DATABASE_URL", None)
    env.pop("SIMULATE_DOTENV_MERGE", None)
    if env_url:
        env["DATABASE_URL"] = env_url
    if dotenv_url:
        env["SIMULATE_DOTENV_MERGE"] = dotenv_url

    # The probe is a constant in this module, so no untrusted input reaches the
    # interpreter. `_setup_django` above escapes this check only because its
    # script is an inline literal.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", _DATABASE_ENGINE_PROBE],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_env_provided_database_url_is_honoured():
    """A DATABASE_URL genuinely supplied by the caller reaches the test suite."""
    result = _database_engine(env_url="postgres://flowforge:flowforge@localhost:5432/flowforge")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "django.db.backends.postgresql"


def test_dotenv_provided_database_url_does_not_drag_the_suite_off_sqlite():
    """A ``.env`` value must not be mistaken for a caller-supplied one.

    ``base`` calls ``read_env``, which copies the file's values into
    ``os.environ``. After that merge a file value is indistinguishable from a
    real environment variable, so the test settings snapshot the process
    environment *before* importing ``base`` and use only that snapshot. Without
    it, any developer with a checked-out ``.env`` could not run the suite at
    all, despite this module documenting itself as needing no external
    services.
    """
    result = _database_engine(dotenv_url="postgres://flowforge:flowforge@localhost:5432/flowforge")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "django.db.backends.sqlite3"
