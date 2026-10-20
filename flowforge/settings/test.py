"""Test settings.

Fast, deterministic, and self-contained: no external services are required.
``DEBUG`` is off so that the production security branches are exercised by
the test suite.
"""

import os
from typing import Any, cast

# Read the raw process environment *before* ``base`` is imported. ``base``
# calls ``environ.Env.read_env(BASE_DIR / ".env")``, which copies every value
# in that file into ``os.environ``, so afterwards a file value is
# indistinguishable from a variable the caller really supplied. Without this
# snapshot, a developer's checked-out ``.env`` silently drags the whole suite
# onto PostgreSQL and contradicts this module's own contract.
#
# CI sets DATABASE_URL as a job-level environment variable, so it is captured
# here too and the CI behaviour is unchanged.
SUPPLIED_DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

os.environ.setdefault("DJANGO_DEBUG", "False")
os.environ.setdefault(
    "DJANGO_SECRET_KEY",
    "test-only-secret-key-with-more-than-fifty-characters-0123456789",
)

# E402: the imports must stay below the ``SUPPLIED_DATABASE_URL`` snapshot,
# because ``base`` merges ``.env`` into ``os.environ`` on import.
from .base import *  # noqa: E402,F403
from .base import BASE_DIR, LOGGING, REST_FRAMEWORK  # noqa: E402

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

# Capture emails instead of sending them.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Prefer SQLite unless the caller genuinely supplied a DATABASE_URL in the
# process environment (CI points the job at PostgreSQL). A value that only
# exists inside ``.env`` is deliberately ignored — see the snapshot above.
if not SUPPLIED_DATABASE_URL:
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

# Throttling is real in production (see base.py), but the bulk API tests disable
# it so they cannot flake on a *cumulative* rate limit across test methods that
# share a user. The behaviour itself is covered by a dedicated test that
# installs the throttle class and a tight rate with `override_settings`.
#
# Removing the *classes* is what disables throttling. Emptying the rates leaves
# `ScopedRateThrottle` installed, and it then raises `ImproperlyConfigured` for
# every view that names a `throttle_scope` — a 500 on each request, which is the
# opposite of disabling the check.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
}
