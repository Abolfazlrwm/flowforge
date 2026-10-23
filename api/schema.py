"""drf-spectacular extensions for authentication schemes DRF does not ship.

The API-key authenticator is a project class, so drf-spectacular has no built-in
security definition for it and warns once per view. Registering one documents the
``X-API-Key`` header (the form the authenticator accepts alongside
``Authorization: Api-Key ...``) and removes the warnings.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    """Document the ``X-API-Key`` header as an OpenAPI ``apiKey`` scheme."""

    target_class = "api.authentication.ApiKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, _auto_schema: Any) -> dict[str, str]:
        """The scheme: a key presented in the ``X-API-Key`` header."""
        return {"type": "apiKey", "in": "header", "name": "X-API-Key"}
