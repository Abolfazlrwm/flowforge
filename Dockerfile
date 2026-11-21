# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: resolve dependencies from uv.lock into an isolated virtualenv
#
# Both base images are pinned by digest, not by tag. A tag is mutable: whoever
# controls the registry can repoint `python:3.13-slim` at a different image, and
# a build that resolves a tag at build time is trusting the registry to still
# mean the same thing. A digest names one exact manifest.
#
# The cost is deliberate and documented: the base image no longer receives
# security updates by itself. Dependabot is configured for the `docker`
# ecosystem and opens a PR when the digest moves; until that PR merges, the
# image is exactly the one that was reviewed. Bump with:
#   docker buildx imagetools inspect python:3.13-slim
# ---------------------------------------------------------------------------
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# README.md is referenced by [project].readme, so it must exist before the
# package metadata is generated.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY manage.py ./
COPY flowforge/ ./flowforge/
COPY core/ ./core/
COPY api/ ./api/

# --locked fails the build if uv.lock is out of date.
# --no-editable installs the project into site-packages, so the runtime image
# does not depend on the bind-mounted source tree.
RUN uv sync --locked --no-editable

# ---------------------------------------------------------------------------
# Production
#
# Same digest as the builder stage: one reviewed base image for the whole file,
# so there is no question of the two stages differing in what they ship.
# ---------------------------------------------------------------------------
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=flowforge.settings.prod \
    PATH="/opt/venv/bin:$PATH"

# The dist-upgrade pulls the current security patches for the base image's
# OS packages (curl, glibc, krb5, openssl, sqlite3, ...) from the Debian
# repos at build time instead of inheriting the digest snapshot's state.
# The digest pin freezes the base to one manifest; this keeps the packages
# on top of it current at every build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

# UID/GID 10001: above the system-user range (KSV-0020/0021) and identical to
# the pod-level runAsUser/runAsGroup the Helm chart sets, so the image user and
# the pod user are the same identity everywhere.
RUN groupadd -g 10001 flowforge \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /app flowforge

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY . .

# The interpreter this image runs is /opt/venv/bin/python, whose pyvenv.cfg sets
# `include-system-site-packages = false`. Everything under the base image's own
# /usr/local/lib/python3.13/site-packages is therefore unreachable — no import
# can resolve to it, and nothing here invokes the base image's `pip` or `uv`.
#
# It is removed anyway, because unreachable is not the same as absent. Trivy
# scans image layers, not runtime import graphs, so those packages still show up
# as findings: urllib3 2.7.0 and msgpack 1.1.2 carry fixable HIGH advisories,
# and the venv already ships urllib3 2.8.0 and msgpack 1.2.3. Deleting the
# directory removes the finding and shrinks what an attacker who gets code
# execution could reach for.
#
# The standard library is a sibling directory (/usr/local/lib/python3.13/...,
# not .../site-packages), so this does not touch it.
RUN rm -rf /usr/local/lib/python3.13/site-packages

# collectstatic runs under production settings so the manifest that
# CompressedManifestStaticFilesStorage expects is generated. Production
# settings refuse to start without a secret key, so an ephemeral one is
# generated for the duration of this build step only.
RUN DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
    DJANGO_ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput \
    && chown -R flowforge:flowforge /app

USER flowforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live/ || exit 1

CMD ["gunicorn", "flowforge.wsgi:application", "-b", "0.0.0.0:8000", "-w", "4"]
