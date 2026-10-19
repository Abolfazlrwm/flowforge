"""Root URL configuration for FlowForge."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("core.urls")),
    path("api/v1/", include("api.v1.urls")),
]
