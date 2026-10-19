"""Test settings.

Fast, deterministic, and self-contained: no external services are required.
``DEBUG`` is off so that the production security branches are exercised by
the test suite.
"""

import os
from typing import Any, cast

os.environ.setdefault("DJANGO_DEBUG", "False")
os.environ.setdefault(
    "DJANGO_SECRET_KEY",
    "test-only-secret-key-with-more-than-fifty-characters-0123456789",
)

from .base import *  # noqa: F403
from .base import BASE_DIR, LOGGING, env

DEBUG = False

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

# Fast password hashing — never use this outside tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# In-process cache so tests do not need Redis.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "flowforge-test",
    },
}

# Run Celery tasks inline.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Capture emails instead of sending them.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Prefer SQLite unless a DATABASE_URL is supplied (CI points at PostgreSQL).
if not env("DATABASE_URL", default=None):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test.sqlite3",
        }
    }

# Keep logs quiet during test runs.
_logging = cast("dict[str, Any]", LOGGING)
_logging["root"] = {"handlers": ["console"], "level": "WARNING"}
LOGGING = _logging
