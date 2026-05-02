"""Per-file coverage floor — gate against any single tracked file slipping
below threshold.

The pyproject ``[tool.coverage.report] fail_under`` setting is GLOBAL: a
single module at 0% can be hidden by everything else being well-covered.
This script reads ``coverage.json`` (produced by pytest's --cov-report=json)
and asserts that EVERY tracked module clears ``PER_FILE_FLOOR``.

There is intentionally no grandfather / allowlist for low-coverage files.
The only escape is the ``omit`` list in pyproject's ``[tool.coverage.run]``
section, which is reserved for files that genuinely cannot be exercised
inside the test process (argparse + sys.exit shells, hardware-bound
services, module-level Qt/loguru bootstrap). Adding to omit is a
deliberate, reviewable change — not a per-file slip.

Run after pytest:
    .venv/Scripts/python.exe -m pytest
    .venv/Scripts/python.exe scripts/check_coverage_per_file.py

Or as a CI step in .github/workflows/tests.yml after the pytest step.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PER_FILE_FLOOR = 80.0   # percent
ROOT = Path(__file__).resolve().parent.parent
COVERAGE_JSON = ROOT / "coverage.json"


def main() -> int:
    if not COVERAGE_JSON.exists():
        print(
            f"ERROR: {COVERAGE_JSON} not found. Run pytest first "
            f"(default addopts in pyproject.toml include --cov-report=json).",
            file=sys.stderr,
        )
        return 2

    data = json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
    files = data.get("files", {})
    if not files:
        print("ERROR: coverage.json has no 'files' section.", file=sys.stderr)
        return 2

    failures: list[tuple[str, float]] = []
    for relpath, info in sorted(files.items()):
        # Normalize to forward slashes for stable display across OSes.
        norm = relpath.replace("\\", "/")
        pct = float(info["summary"]["percent_covered"])
        if pct < PER_FILE_FLOOR:
            failures.append((norm, pct))

    if failures:
        print(
            f"\nPer-file coverage floor ({PER_FILE_FLOOR:.0f}%) "
            f"violations — {len(failures)} file(s):"
        )
        for path, pct in failures:
            print(f"  {pct:>5.1f}%   {path}")
        print()
        print("For each violation:")
        print("  - Add tests until the file clears the floor, OR")
        print("  - If the file genuinely cannot be exercised in tests, add")
        print(
            "    its path to `[tool.coverage.run] omit` in pyproject.toml"
        )
        print("    with a one-line comment explaining why.")
        return 1

    print(
        f"\n[ok] All {len(files)} tracked files clear the per-file "
        f"coverage floor ({PER_FILE_FLOOR:.0f}%)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
