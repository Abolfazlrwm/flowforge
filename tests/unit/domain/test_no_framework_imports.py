"""Enforce that the domain layer stays framework-free.

Two independent checks, because they fail differently:

1. An **AST scan** names the offending file and line, which is what a
   contributor needs. It catches an import even inside a function or a
   ``try/except ImportError`` block, both of which a runtime check would miss.
2. A **subprocess import** with ``django`` made unimportable proves the property
   at runtime, so the rule cannot be satisfied by a static-analysis blind spot
   (a dynamic import, an ``importlib`` call, a package that re-exports).

If someone adds a framework import to the domain, both fail.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

DOMAIN_ROOT = Path(__file__).resolve().parents[3] / "core" / "domain"

FORBIDDEN_ROOTS = frozenset(
    {
        "django",
        "rest_framework",
        "celery",
        "kombu",
        "redis",
        "psycopg",
        "psycopg2",
        "gunicorn",
    }
)


def _iter_modules() -> list[Path]:
    """Every Python module in the domain package."""
    return sorted(DOMAIN_ROOT.rglob("*.py"))


def _forbidden_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """Find imports of a forbidden root anywhere in the module."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_ROOTS:
                    found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            # A relative import has no module, or a module that starts with a
            # dot; neither can reach a third-party package.
            if not node.module or node.level:
                continue
            root = node.module.split(".")[0]
            if root in FORBIDDEN_ROOTS:
                found.append((node.lineno, node.module))
    return found


def test_domain_package_exists() -> None:
    """Guard against the scan silently passing on an empty directory."""
    modules = _iter_modules()
    assert modules, f"no modules found under {DOMAIN_ROOT}"


def test_domain_has_no_framework_imports() -> None:
    """No module under core/domain may import a framework or a driver."""
    offenders: list[str] = []
    for path in _iter_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, name in _forbidden_imports(tree):
            rel = path.relative_to(DOMAIN_ROOT.parents[1])
            offenders.append(f"{rel}:{lineno} imports {name}")
    assert not offenders, "the domain layer must not import frameworks:\n" + "\n".join(offenders)


def test_domain_imports_without_django_installed() -> None:
    """Prove it at runtime: import the package with ``django`` unimportable."""
    script = """
import sys

class _Blocked:
    def find_module(self, name, path=None):
        if name == "django" or name.startswith("django."):
            raise ImportError("django is deliberately unimportable in this test")
        return None

sys.meta_path.insert(0, _Blocked())
sys.modules.pop("django", None)

import core.domain  # noqa: F401
print("domain imported without django")
"""
    result = subprocess.run(  # noqa: S603 - argv is a literal, not user input
        [sys.executable, "-c", script],
        cwd=DOMAIN_ROOT.parents[1],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        "importing core.domain failed with django unavailable:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "domain imported without django" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        "import django",
        "import django.db",
        "from django.db import models",
        "def f():\n    import django",
        "try:\n    import django\nexcept ImportError:\n    django = None",
    ],
)
def test_scanner_detects_forbidden_imports(source: str) -> None:
    """The scanner must actually catch the shapes it claims to catch.

    Without this, a broken scanner would make every other test here pass
    vacuously — the failure mode this whole file exists to prevent.
    """
    tree = ast.parse(source)
    assert _forbidden_imports(tree), f"scanner missed a forbidden import in: {source!r}"


@pytest.mark.parametrize(
    "source",
    [
        "import json",
        "from collections.abc import Iterable",
        "from core.domain.states import TaskRunState",
        "from .states import TaskRunState",
    ],
)
def test_scanner_allows_legitimate_imports(source: str) -> None:
    """The scanner must not produce false positives."""
    tree = ast.parse(source)
    assert not _forbidden_imports(tree), f"scanner rejected a legitimate import: {source!r}"
