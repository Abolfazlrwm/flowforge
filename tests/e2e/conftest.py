"""Fixtures for the end-to-end suite.

The suite *skips* -- rather than fails -- when there is no live stack or no
token, so a developer running the whole suite locally (which has neither) is not
blocked. In CI the ``docker`` job provides both and the suite runs for real.
"""

from __future__ import annotations

import urllib.request

import pytest
from flowforge_client import FlowForge

from ._support import base_url, token


@pytest.fixture(scope="session")
def root() -> str:
    """The root URL of the deployment under test."""
    return base_url()


@pytest.fixture(scope="session")
def api_token() -> str:
    """The credential, or skip when it is not provided."""
    tok = token()
    if not tok:
        pytest.skip("FLOWFORGE_E2E_TOKEN is not set; the e2e suite needs a live stack")
    return tok


@pytest.fixture(scope="session")
def live(root: str) -> str:
    """Prove the stack is reachable, or skip when it is not."""
    try:
        with urllib.request.urlopen(f"{root}/health/live/", timeout=5) as response:
            if response.status != 200:
                pytest.skip(f"liveness probe at {root} returned {response.status}")
    except OSError as exc:
        pytest.skip(f"no FlowForge stack reachable at {root}: {exc}")
    return root


@pytest.fixture(scope="session")
def client(live: str, api_token: str) -> FlowForge:
    """A configured SDK client against the live, ready stack."""
    sdk = FlowForge(live, token=api_token)
    readiness = sdk.health()
    assert readiness.get("status") == "ready", f"stack is not ready: {readiness}"
    return sdk
