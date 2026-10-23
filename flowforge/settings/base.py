"""Base settings shared by all environments.

Environment selection is done via ``DJANGO_SETTINGS_MODULE``:

- ``flowforge.settings.dev``  — local development (DEBUG on)
- ``flowforge.settings.test`` — automated tests
- ``flowforge.settings.prod`` — production (secure defaults enforced)
"""

from datetime import timedelta
from pathlib import Path

import environ
import structlog
from django.core.exceptions import ImproperlyConfigured

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_SECURE_SSL_REDIRECT=(bool, False),
    DJANGO_SECURE_HSTS_SECONDS=(int, 0),
    DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=(bool, False),
    DJANGO_SECURE_HSTS_PRELOAD=(bool, False),
    DJANGO_SECURE_REFERRER_POLICY=(str, "same-origin"),
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
DEBUG: bool = env("DJANGO_DEBUG")

# SECRET_KEY is mandatory whenever DEBUG is off. In development a well-known
# insecure key is allowed so that `runserver` works out of the box, but it is
# never acceptable in production.
_secret_key: str | None = env("DJANGO_SECRET_KEY", default=None)

if not _secret_key:
    if DEBUG:
        _secret_key = "django-insecure-dev-only-key-do-not-use-in-production"  # noqa: S105
    else:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is required when DEBUG is off. "
            "Generate one with: python -c "
            "'from django.core.management.utils import get_random_secret_key; "
            "print(get_random_secret_key())'"
        )

SECRET_KEY: str = _secret_key

ALLOWED_HOSTS: list[str] = env("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    "django_structlog",
    "core",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_structlog.middlewares.RequestMiddleware",
]

ROOT_URLCONF = "flowforge.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "flowforge.wsgi.application"

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default="postgres://flowforge:flowforge@localhost:5432/flowforge",
    ),
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Security (baseline — prod.py hardens these further)
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT")
SECURE_HSTS_SECONDS = env("DJANGO_SECURE_HSTS_SECONDS")
SECURE_HSTS_INCLUDE_SUBDOMAINS = env("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = env("DJANGO_SECURE_HSTS_PRELOAD")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_REFERRER_POLICY = env("DJANGO_SECURE_REFERRER_POLICY")
X_FRAME_OPTIONS = "DENY"

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    # JWT first, then DRF's own token. Both are supported because they fail
    # differently: a JWT is short-lived and stateless, a token is revocable by
    # deleting a row. Clients that need immediate revocation use the token.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # An `Api-Key` header selects this; with no such header it returns None
        # and DRF falls through to the next authenticator, so token and JWT
        # callers are unaffected.
        "api.authentication.ApiKeyAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # A view that forgets to declare a permission is authenticated-only, never
    # public. Every view also names its class explicitly so the rule is visible
    # where it is applied.
    "DEFAULT_PERMISSION_CLASSES": [
        "api.permissions.IsAuthenticatedAndActive",
    ],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.StandardCursorPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # One envelope for every failure. See `api/errors.py`.
    "EXCEPTION_HANDLER": "api.errors.api_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    # Per-scope rates. The scopes are declared on the views that use them, so a
    # rate is never a number with no owner.
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",
        "trigger": "60/min",
        "read": "300/min",
    },
}

# ---------------------------------------------------------------------------
# Auth tokens
# ---------------------------------------------------------------------------
# JWT access tokens are short-lived because they cannot be revoked; the refresh
# token is what a client exchanges. `ROTATE_REFRESH_TOKENS` plus
# `BLACKLIST_AFTER_ROTATION` would need the token_blacklist app, which adds
# tables for a property (revocation) that DRF's plain token already provides —
# so it is deliberately not enabled here.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "FlowForge API",
    "DESCRIPTION": (
        "Workflow orchestration for DAG-shaped jobs.\n\n"
        'Every failure is returned as `{"error": {"code", "message", "details"}}`.'
    ),
    "VERSION": "0.4.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # The schema is committed and checked in CI, so a drift between code and
    # `docs/api/openapi.yaml` fails the build rather than being discovered by a
    # client.
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
    # NOTE: `WorkflowRun.state` and `TaskRun.state` are `StrEnum`s whose value
    # sets overlap, so drf-spectacular cannot always derive distinct component
    # names and emits one cosmetic "enum naming collision" warning when it
    # generates this schema. It is deterministic, does not change the output
    # file, and does not affect the committed-schema drift check below.
}

# ---------------------------------------------------------------------------
# Message broker for the publisher (ADR-011)
# ---------------------------------------------------------------------------
# The publisher process reads transactional-outbox rows and delivers them here.
# This is the one broker FlowForge talks to: the worker (Phase 5) consumes from
# it. `amqp://`/`amqps://` selects the RabbitMQ publisher; tests and local runs
# can point at a fake in-process publisher via the `fake://` scheme.
RABBITMQ_URL: str = env("RABBITMQ_URL", default="amqp://guest:guest@localhost:5672/")

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
    },
}

# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------
HEALTH_CHECK_BROKER_URL = env("RABBITMQ_URL", default="amqp://guest:guest@localhost:5672/")

# ---------------------------------------------------------------------------
# Logging / Structlog
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": structlog.processors.JSONRenderer(),
            "foreign_pre_chain": [
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.ExtraAdder(),
            ],
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "django_structlog": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "flowforge": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
