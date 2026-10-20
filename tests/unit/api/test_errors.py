"""The error envelope, and the mapping that produces it.

Two layers are tested separately on purpose:

* the **shape** every client depends on, asserted through the API so it is the
  real rendered body rather than a unit-level imitation;
* the **mapping**, asserted by calling the handler directly. A domain error that
  is raised but never mapped is the defect this module exists to prevent — the
  service can raise a perfectly good exception and still return a 500.
"""

from __future__ import annotations

import uuid
from typing import Any

from api import errors
from api.errors import ErrorCode, api_exception_handler, error_body
from core.domain.schema import DefinitionSchemaError
from core.repositories.idempotency import IdempotencyMismatchError
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.test import APIClient


class TestEnvelopeShape:
    """One shape for every failure, so a client writes its error handling once."""

    def test_a_failure_has_a_code_and_a_message(self, user_client: APIClient) -> None:
        """The code is the contract; the message is for a human."""
        response = user_client.get(reverse("run-detail", kwargs={"pk": uuid.uuid4()}))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert set(response.data) == {"error"}
        assert response.data["error"]["code"] == "not_found"
        assert isinstance(response.data["error"]["message"], str)

    def test_details_are_omitted_when_there_are_none(self, user_client: APIClient) -> None:
        """A client can branch on presence rather than on emptiness."""
        response = user_client.get(reverse("run-detail", kwargs={"pk": uuid.uuid4()}))

        assert "details" not in response.data["error"]

    def test_a_validation_failure_carries_field_details(
        self, user_client: APIClient, published: Any
    ) -> None:
        """The field-level detail is what makes a 400 actionable."""
        response = user_client.post(reverse("run-list"), {"name": "etl"}, format="json")

        assert response.data["error"]["code"] == "validation_error"
        assert "version" in response.data["error"]["details"]


class TestMapping:
    """Each branch of the handler, called directly."""

    def test_an_idempotency_mismatch_is_422(self) -> None:
        """A plain exception that nothing else would map.

        Without this branch it falls through to the 500 path, which is what the
        views docstring promises it does not do.
        """
        response = api_exception_handler(IdempotencyMismatchError("k"), {})

        assert response is not None
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert response.data["error"]["code"] == "idempotency_mismatch"

    def test_a_schema_error_is_400(self) -> None:
        """A malformed payload is the caller's mistake, not the server's."""
        response = api_exception_handler(DefinitionSchemaError(("$.tasks: bad",)), {})

        assert response is not None
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["code"] == "validation_error"
        assert "$.tasks: bad" in response.data["error"]["message"]

    def test_an_unknown_exception_is_not_swallowed(self) -> None:
        """Returning None is what lets Django's own 500 path run.

        Dressing a defect up as a tidy JSON envelope would hide it behind a
        response that looks intentional.
        """
        assert api_exception_handler(ValueError("boom"), {}) is None

    def test_a_drf_exception_keeps_its_own_code(self) -> None:
        """A DRF exception's own code wins over the status-derived default.

        `authentication_failed` and `authentication_required` share a status but
        mean different things to a client deciding whether to refresh.
        """
        response = api_exception_handler(AuthenticationFailed(), {})

        assert response is not None
        assert response.data["error"]["code"] == "authentication_failed"


class TestDrfShapes:
    """DRF produces three bodies; all three must become one envelope.

    Exercised through real `ValidationError`s rather than by importing the
    private helper, so the test covers the shape DRF actually produces.
    """

    def test_a_non_field_error_becomes_the_message(self) -> None:
        """A single cross-field failure is a message, not a field map."""
        response = api_exception_handler(ValidationError({"non_field_errors": ["broken"]}), {})

        assert response is not None
        assert response.data["error"]["message"] == "broken"
        assert "details" not in response.data["error"]

    def test_a_list_body_becomes_field_details(self) -> None:
        """A list has no field names, so it is passed through as details."""
        response = api_exception_handler(ValidationError(["a", "b"]), {})

        assert response is not None
        assert response.data["error"]["message"] == "One or more fields were invalid."
        assert response.data["error"]["details"] == ["a", "b"]

    def test_a_scalar_body_is_a_defensive_fallback(self) -> None:
        """A scalar body is unreachable through the handler, and still handled.

        DRF wraps a scalar detail in ``{"detail": ...}``, so this shape does not
        occur today. It is kept because `_normalise` is a total function over
        whatever a future DRF version hands it, and a missing fallback would be
        an ``AttributeError`` inside the error path itself.
        """
        assert errors._normalise("plain") == ("plain", None)


class TestErrorBody:
    """The envelope builder itself."""

    def test_details_are_omitted_not_null(self) -> None:
        """`details: null` would force every client to null-check."""
        body = error_body(ErrorCode.NOT_FOUND, "gone")

        assert body == {"error": {"code": "not_found", "message": "gone"}}

    def test_details_are_included_when_supplied(self) -> None:
        """Empty-but-present is distinguishable from absent."""
        body = error_body(ErrorCode.VALIDATION_ERROR, "bad", {"name": ["required"]})

        assert body["error"]["details"] == {"name": ["required"]}
