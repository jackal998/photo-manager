"""Per-file coverage floor — gate against any tracked file slipping below
threshold on the unit test layer.

Scope: layer-1 tests only (unit + mock-based, runs on CI). Higher layers
(real-binary integration tests, full-GUI qa-explore) cover boundary
behaviors mocks cannot reach. See docs/testing.md for the layer model
and what each layer catches.

Why 70% (not 80%)?
  Per-file 80% pushes contributors to write tests that exercise defensive
  `except: pass` branches and ImportError fallbacks by mocking the
  dependencies. Those tests cover code without catching bugs — gaming
  the metric is then the only honest way to clear CI. 70% leaves room for
  each file's genuinely-defensive tail (ImportError guards, pathological-
  input fallbacks) while still failing on any module whose primary logic
  is untested.

Why no per-file allowlist / grandfather list?
  The only escape valve is the ``omit`` list in pyproject's
  ``[tool.coverage.run]``. Each ``omit`` entry MUST justify itself with a
  comment (why it can't run in tests) and ideally a pointer to where it
  IS covered (integration suite, qa-explore scenario, manual). Adding a
  path to omit is a deliberate, reviewable change — not a silent slip.

Run after pytest:
    .venv/Scripts/python.exe -m pytest
    .venv/Scripts/python.exe scripts/check_coverage_per_file.py

CI runs this as a step right after pytest in tests.yml.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PER_FILE_FLOOR = 70.0   # percent — see module docstring for rationale
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
