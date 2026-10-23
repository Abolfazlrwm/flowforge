"""API v1 URL configuration.

A version in the path, not in a header. A header-based version is invisible in
logs, browser history and a bug report; `/api/v1/` is not. When v2 exists it
gets a new module and a new prefix, and v1 keeps working until it is explicitly
retired.
"""

from __future__ import annotations

from django.urls import URLPattern, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from api.views import (
    ApiKeyListCreateView,
    ApiKeyRevokeView,
    ApiKeyRotateView,
    MeView,
    RunDetailView,
    RunEventsView,
    RunListCreateView,
    TaskRunRequeueView,
    WorkflowDetailView,
    WorkflowListCreateView,
)

urlpatterns: list[URLPattern] = [
    # --- authentication -----------------------------------------------------
    # Two mechanisms, both real: a DRF token is revocable by deleting a row, a
    # JWT is short-lived and stateless. Clients pick per their revocation needs.
    path("auth/token/", obtain_auth_token, name="auth-token"),
    path("auth/jwt/", TokenObtainPairView.as_view(), name="auth-jwt"),
    path("auth/jwt/refresh/", TokenRefreshView.as_view(), name="auth-jwt-refresh"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    # --- definitions --------------------------------------------------------
    path("workflows/", WorkflowListCreateView.as_view(), name="workflow-list"),
    path(
        "workflows/<str:name>/<int:version>/",
        WorkflowDetailView.as_view(),
        name="workflow-detail",
    ),
    # --- runs ---------------------------------------------------------------
    path("runs/", RunListCreateView.as_view(), name="run-list"),
    path("runs/<uuid:pk>/", RunDetailView.as_view(), name="run-detail"),
    path("runs/<uuid:pk>/events/", RunEventsView.as_view(), name="run-events"),
    # --- task runs ----------------------------------------------------------
    path(
        "task-runs/<uuid:pk>/requeue/",
        TaskRunRequeueView.as_view(),
        name="task-run-requeue",
    ),
    # --- api keys -----------------------------------------------------------
    # The plaintext key is returned once, at creation or rotation. Listing only
    # ever shows metadata.
    path("api-keys/", ApiKeyListCreateView.as_view(), name="api-key-list"),
    path(
        "api-keys/<uuid:pk>/rotate/",
        ApiKeyRotateView.as_view(),
        name="api-key-rotate",
    ),
    path(
        "api-keys/<uuid:pk>/revoke/",
        ApiKeyRevokeView.as_view(),
        name="api-key-revoke",
    ),
    # --- schema -------------------------------------------------------------
    # The schema is committed to `docs/api/openapi.yaml` and CI fails if it has
    # drifted. Serving it here is for humans reading the docs site.
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
