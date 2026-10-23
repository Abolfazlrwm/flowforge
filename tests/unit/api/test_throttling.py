"""Throttling: the scopes have rates, and the 429 path is real.

`flowforge.settings.test` removes `DEFAULT_THROTTLE_CLASSES`, because a
cumulative rate limit shared by every test method makes unrelated tests flaky.
The consequence is that nothing else in the suite exercises a throttle, so a
scope with no configured rate — which is a 500 on every request, not a disabled
check — would ship unnoticed. This module closes that gap twice: once by
asserting every declared scope has a rate, and once by installing the real class
and a rate of one per minute.

DRF captures `throttle_classes` and `THROTTLE_RATES` as class attributes at
import time, so `override_settings` does not affect them. The class attributes
are patched directly instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from api.views import (
    MeView,
    RunDetailView,
    RunListCreateView,
    WorkflowDetailView,
    WorkflowListCreateView,
)
from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

#: Every view that names a throttle scope. Adding a view with a new scope
#: without adding it here is the one way this check can be bypassed, so the
#: list is asserted to be non-empty below.
THROTTLED_VIEWS: tuple[type[APIView], ...] = (
    MeView,
    WorkflowListCreateView,
    WorkflowDetailView,
    RunListCreateView,
    RunDetailView,
)


@pytest.fixture(autouse=True)
def _empty_throttle_cache() -> Iterator[None]:
    """Throttle counters live in the cache; a shared cache would leak counts."""
    cache.clear()
    yield
    cache.clear()


def _declared_scopes(view_class: type[APIView]) -> set[str]:
    """The scopes ``view_class`` would use, for both a read and a write."""
    factory = RequestFactory()
    scopes: set[str] = set()
    for method in ("GET", "POST"):
        view = view_class()
        view.request = factory.generic(method, "/api/v1/")
        view.get_throttles()
        scope = getattr(view, "throttle_scope", None)
        if scope:
            scopes.add(scope)
    return scopes


class TestScopesHaveRates:
    """A scope without a rate raises `ImproperlyConfigured` on every request."""

    def test_every_declared_scope_has_a_rate(self) -> None:
        """The settings that apply to tests must cover what the views name."""
        rates = set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        declared = set().union(*(_declared_scopes(view) for view in THROTTLED_VIEWS))

        assert declared, "no scopes found — the helper has drifted from the views"
        assert declared <= rates, f"scopes with no rate: {sorted(declared - rates)}"

    def test_the_views_use_more_than_one_scope(self) -> None:
        """A single scope would mean the read/write split was never applied."""
        declared = set().union(*(_declared_scopes(view) for view in THROTTLED_VIEWS))

        assert {"read", "trigger"} <= declared

    def test_the_shipped_rates_are_the_documented_policy(self) -> None:
        """The defaults are the shipped policy; a typo here silently loosens it.

        The rates are configurable through the environment because every
        deployment has a different notion of "normal", and the load-test job
        raises them so the benchmark measures the engine rather than the throttle.
        That makes the default values load-bearing in a way they were not before:
        an unset variable must still yield the documented limit.
        """
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

        assert rates["auth"] == "10/min"
        assert rates["trigger"] == "60/min"
        assert rates["read"] == "300/min"


class TestTriggerThrottle:
    """The 429 path, with the real throttle class installed."""

    def test_the_second_trigger_in_the_window_is_429(
        self,
        user_client: APIClient,
        published: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One request per minute means the second is refused."""
        monkeypatch.setattr(RunListCreateView, "throttle_classes", [ScopedRateThrottle])
        monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"trigger": "1/min"})
        body = {"name": "etl", "version": 1}

        first = user_client.post(reverse("run-list"), body, format="json")
        second = user_client.post(reverse("run-list"), body, format="json")

        assert first.status_code == status.HTTP_202_ACCEPTED
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert second.data["error"]["code"] == "throttled"

    def test_listing_is_not_throttled_by_the_trigger_scope(
        self,
        user_client: APIClient,
        published: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A trigger budget must not be spent by reads, and vice versa."""
        monkeypatch.setattr(RunListCreateView, "throttle_classes", [ScopedRateThrottle])
        monkeypatch.setattr(
            ScopedRateThrottle,
            "THROTTLE_RATES",
            {"trigger": "1/min", "read": "100/min"},
        )

        user_client.post(reverse("run-list"), {"name": "etl", "version": 1}, format="json")
        listing = user_client.get(reverse("run-list"))

        assert listing.status_code == status.HTTP_200_OK
