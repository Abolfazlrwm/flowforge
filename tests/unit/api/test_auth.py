"""Authentication and `/auth/me/`.

Two mechanisms are supported because they fail differently, and one test class
per mechanism is what keeps them honest:

* a **DRF token** can be revoked by deleting a row, so deactivation and
  revocation are immediately observable;
* a **JWT** is stateless and short-lived, so a valid signature is all the
  server checks and a deleted account is not noticed until the token expires.

The `is_active` check in `IsAuthenticatedAndActive` is what makes the DRF token
behave. Without it, a user deactivated after receiving a token keeps working
until the token is deleted, which is not the point of a revocable token.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient


class TestTokenAuth:
    """A DRF token, and what deleting it does."""

    def test_a_token_grants_access(self, db: None, api_client: APIClient) -> None:
        """Token 401 without, 200 with."""
        user = User.objects.create_user("alice", password="pw12345!")
        url = reverse("auth-me")

        unauthenticated = api_client.get(url)
        assert unauthenticated.status_code == status.HTTP_401_UNAUTHORIZED

        api_client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        authenticated = api_client.get(url)
        assert authenticated.status_code == status.HTTP_200_OK

    def test_a_deleted_token_denies_access(self, db: None, api_client: APIClient) -> None:
        """Revocation is the reason to choose a token over a JWT."""
        user = User.objects.create_user("alice", password="pw12345!")
        token = Token.objects.create(user=user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        assert api_client.get(reverse("auth-me")).status_code == status.HTTP_200_OK

        token.delete()
        assert api_client.get(reverse("auth-me")).status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_garbage_token_is_unauthorised(self, db: None, api_client: APIClient) -> None:
        """A malformed token is an authentication failure, not a crash."""
        api_client.credentials(HTTP_AUTHORIZATION="Token not-a-real-key")
        response = api_client.get(reverse("auth-me"))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_user_is_denied(self, db: None, api_client: APIClient) -> None:
        """`is_active` is checked here, not left to token expiry."""
        user = User.objects.create_user("alice", password="pw12345!")
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        assert api_client.get(reverse("auth-me")).status_code == status.HTTP_200_OK

        user.is_active = False
        user.save(update_fields=["is_active"])
        assert api_client.get(reverse("auth-me")).status_code == status.HTTP_401_UNAUTHORIZED


class TestJwtAuth:
    """A JWT, obtained by exchange and refreshed."""

    def test_a_jwt_grants_access(self, db: None, api_client: APIClient) -> None:
        """Exchange credentials for a bearer token, then use it."""
        User.objects.create_user("alice", password="pw12345!")
        issue = api_client.post(
            reverse("auth-jwt"),
            {"username": "alice", "password": "pw12345!"},
            format="json",
        )
        assert issue.status_code == status.HTTP_200_OK
        assert {"access", "refresh"} <= set(issue.data)

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue.data['access']}")
        assert api_client.get(reverse("auth-me")).status_code == status.HTTP_200_OK

    def test_wrong_credentials_are_rejected(self, db: None, api_client: APIClient) -> None:
        """Bad credentials are 401 with the `authentication_failed` code, not 500."""
        User.objects.create_user("alice", password="pw12345!")
        response = api_client.post(
            reverse("auth-jwt"),
            {"username": "alice", "password": "wrong"},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data["error"]["code"] == "authentication_failed"

    def test_a_refreshed_access_token_grants_access(self, db: None, api_client: APIClient) -> None:
        """The refresh endpoint issues a usable access token."""
        User.objects.create_user("alice", password="pw12345!")
        issue = api_client.post(
            reverse("auth-jwt"),
            {"username": "alice", "password": "pw12345!"},
            format="json",
        )
        refresh = api_client.post(
            reverse("auth-jwt-refresh"),
            {"refresh": issue.data["refresh"]},
            format="json",
        )
        assert refresh.status_code == status.HTTP_200_OK

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.data['access']}")
        assert api_client.get(reverse("auth-me")).status_code == status.HTTP_200_OK


class TestMe:
    """The caller's own account, and the envelope it is wrapped in."""

    def test_me_returns_the_caller_only(self, db: None, api_client: APIClient) -> None:
        """The response is the authenticated user, not a guess from the request."""
        alice = User.objects.create_user("alice", password="pw12345!")
        User.objects.create_user("bob", password="pw12345!")
        api_client.force_authenticate(user=alice)

        payload = api_client.get(reverse("auth-me")).data
        assert payload["username"] == "alice"
        assert payload["id"] == alice.pk
