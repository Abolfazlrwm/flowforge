"""Summarise a Trivy JSON report: how many findings are fixable, and by what.

Usage: python trivy_summary.py <trivy.json>
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    """Print how many findings a Trivy JSON report has, and how many are fixable."""
    path = Path(sys.argv[1])
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    fixed: list[tuple[str, str, str, str, str]] = []
    unfixed: list[tuple[str, str, str, str]] = []
    per_target: Counter[str] = Counter()

    for result in data.get("Results") or []:
        target = result.get("Target", "?")
        for vuln in result.get("Vulnerabilities") or []:
            per_target[target] += 1
            row = (
                vuln["VulnerabilityID"],
                vuln["PkgName"],
                vuln.get("InstalledVersion", "?"),
                vuln.get("FixedVersion", ""),
                vuln.get("Severity", "?"),
            )
            if row[3]:
                fixed.append(row)
            else:
                unfixed.append((*row[:3], row[4]))

    total = len(fixed) + len(unfixed)
    print(f"HIGH/CRITICAL total: {total}  (fixable: {len(fixed)}, no fix: {len(unfixed)})")
    print()
    for target, count in per_target.most_common():
        print(f"  {count:4d}  {target}")
    print()
    if fixed:
        print("Fixable (a package upgrade resolves these):")
        for vid, pkg, inst, fix, sev in sorted(fixed, key=lambda r: (r[1], r[0])):
            print(f"  {vid:20s} {pkg:18s} {inst:12s} -> {fix:14s} [{sev}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
