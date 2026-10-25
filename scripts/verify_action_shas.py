"""Verify that every action pinned in a GitHub workflow resolves to a real commit.

Reads the workflow file, extracts `uses: owner/repo@sha`, and asks the GitHub
API for each commit. A pin that does not resolve is a workflow that cannot run,
and a pin that resolves to something unexpected is worse — this exists so the
SHAs in this repository are never typed from memory.

Usage: python scripts/verify_action_shas.py [workflow.yml ...]
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

USES = re.compile(r"uses:\s*([A-Za-z0-9._/-]+)@([0-9a-f]{40})\s*(?:#\s*(\S+))?")


def candidate_repos(reference: str) -> list[str]:
    """The repos a `uses:` reference could point at.

    A reference is either ``owner/repo@ref`` or ``owner/repo/subdir@ref`` — the
    second form is how a monorepo ships several actions (``github/codeql-action/
    upload-sarif`` is the common one). The two are indistinguishable from the
    string alone, so both are tried and the first that resolves wins.
    """
    parts = reference.split("/")
    if len(parts) <= 2:
        return [reference]
    # owner/repo first: it is the overwhelmingly common shape.
    return ["/".join(parts[:2]), reference]


def gh_api(path: str, attempts: int = 4) -> dict:
    """Call the GitHub API through `gh`, which already holds a token.

    Retries: an unauthenticated-shaped failure (rate limit, or a 5xx from the
    API) returns no JSON at all, and treating that as "the pin is bad" would
    report a false alarm on a valid pin. Only a definitive 404 is believed.
    """
    for attempt in range(1, attempts + 1):
        # `gh` is the only executable here and the path is built from the
        # repository's own workflow file, so there is no untrusted input on the
        # command line. The absolute path matters: a bare `gh` would resolve
        # through PATH, which is attacker-controlled on a compromised runner.
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                shutil.which("gh") or "gh",
                "api",
                path,
                "-H",
                "Accept: application/vnd.github+json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            if attempt == attempts:
                return {}
            time.sleep(attempt)
            continue
        if data.get("sha") or "Not Found" in str(data.get("message", "")):
            return data
        if attempt == attempts:
            return {}
        time.sleep(attempt)
    return {}


def main() -> int:
    """Verify every pinned action SHA in the given workflow files."""
    paths = sys.argv[1:] or [".github/workflows/ci.yml"]
    failures = 0
    checked: set[tuple[str, str, str]] = set()

    for raw in paths:
        text = Path(raw).read_text(encoding="utf-8")
        for reference, sha, comment in USES.findall(text):
            key = (reference, sha, comment or "")
            if key in checked:
                continue
            checked.add(key)
            data: dict = {}
            resolved_repo = ""
            for repo in candidate_repos(reference):
                data = gh_api(f"repos/{repo}/commits/{sha}")
                if data.get("sha"):
                    resolved_repo = repo
                    break
            message = (data.get("commit", {}).get("message") or "").splitlines()
            headline = message[0][:44] if message else data.get("message", "NO RESPONSE")
            ok = bool(data.get("sha"))
            status = "ok " if ok else "BAD"
            if not ok:
                failures += 1
            print(
                f"[{status}] {reference:40s} {sha}  {comment or '-':10s} "
                f"{resolved_repo or reference} {headline}"
            )

    print()
    print(f"{len(checked)} distinct pins checked, {failures} unresolved")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
