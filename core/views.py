"""Core views for FlowForge."""

from django.http import HttpRequest, JsonResponse
from django.views import View


class HealthCheckView(View):
    """Return service health status."""

    def get(self, request: HttpRequest) -> JsonResponse:  # noqa: ARG002
        """Handle GET requests."""
        return JsonResponse({"status": "ok", "service": "flowforge"})
