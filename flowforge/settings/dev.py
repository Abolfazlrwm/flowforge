"""Development settings.

DEBUG is on, a throwaway SECRET_KEY is permitted, and services default to
localhost so a fresh checkout can run with ``python manage.py runserver``.
"""

import os
from typing import Any, cast

os.environ.setdefault("DJANGO_DEBUG", "True")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

from .base import *  # noqa: F403
from .base import LOGGING, env

DEBUG = True

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1"],
)

# Relax HTTPS-only behaviour locally.
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Human-readable logs during development.
_logging = cast("dict[str, Any]", LOGGING)
_logging["formatters"]["console"] = {
    "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
}
_logging["handlers"]["console"] = {
    "class": "logging.StreamHandler",
    "formatter": "console",
}
LOGGING = _logging
