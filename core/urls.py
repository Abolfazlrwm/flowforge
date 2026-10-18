"""Core URL configuration."""

from django.urls import path

from core.views import LivenessView, ReadinessView

urlpatterns = [
    path("live/", LivenessView.as_view(), name="liveness"),
    path("ready/", ReadinessView.as_view(), name="readiness"),
]
