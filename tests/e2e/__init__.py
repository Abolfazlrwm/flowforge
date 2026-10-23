"""End-to-end tests package.

The Python SDK is a sibling distribution the application does not install. It is
put on ``sys.path`` here -- at package import, before any test module or conftest
imports ``flowforge_client`` -- so the suite exercises the real client and CLI.
"""

from __future__ import annotations

import sys

from ._support import SDK_DIR

if SDK_DIR not in sys.path:
    sys.path.insert(0, SDK_DIR)
