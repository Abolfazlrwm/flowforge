"""TaskForge project package."""

from taskforge.celery import app as celery_app

__all__ = ("celery_app",)
