"""Check that docs/testing.md's per-module tables name files that exist.

The per-module coverage map is the canonical answer to "what's covered,
what's not, what's the residual risk" (see the doc's own Maintenance
section). A row for a module that no longer exists is worse than a
missing row: it reads as coverage.

What this checks
----------------
Every leading table cell in the "Per-module coverage map" section that
looks like a repo path (``| `scanner/media.py` | …``) must correspond to
a tracked file, a tracked directory, or a tracked glob. Rows whose first
cell is not a path (prose rows, phase tables) are ignored.

Usage
-----
    python scripts/check_testing_doc_modules.py

Exit 0 when every listed module resolves; exit 1 with the offending rows
otherwise.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "testing.md"

_SECTION_START = "## Per-module coverage map"
_SECTION_END_RE = re.compile(r"^## (?!Per-module)")

# A first table cell of the form  | `some/path.py` |  or  | `some/dir/` |
_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|")

# Paths that are legitimately a glob / family rather than one file.
_GLOB_CHARS = ("*", "{")


def _tracked() -> set[str]:
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=REPO, text=True
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def _looks_like_a_path(cell: str) -> bool:
    """Only judge cells that are plainly repo paths.

    Keeps the check from firing on rows keyed by a symbol name, a phase
    label, or a probe class.
    """
    if " " in cell:
        return False
    return "/" in cell or cell.endswith((".py", ".ts", ".tsx", ".yml", ".md"))


def _resolves(cell: str, tracked: set[str]) -> bool:
    if any(ch in cell for ch in _GLOB_CHARS):
        stem = cell.split("*", 1)[0].split("{", 1)[0]
        return any(p.startswith(stem) for p in tracked)
    if cell.endswith("/"):
        return any(p.startswith(cell) for p in tracked)
    if cell in tracked:
        return True
    # A directory named without its trailing slash.
    return any(p.startswith(cell + "/") for p in tracked)


def main() -> int:
    lines = DOC.read_text(encoding="utf-8").splitlines()
    tracked = _tracked()

    inside = False
    checked = 0
    missing: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, start=1):
        if line.startswith(_SECTION_START):
            inside = True
            continue
        if inside and _SECTION_END_RE.match(line):
            break
        if not inside:
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        cell = m.group(1)
        if not _looks_like_a_path(cell):
            continue
        checked += 1
        if not _resolves(cell, tracked):
            missing.append((lineno, cell))

    if missing:
        print(f"docs/testing.md per-module map: {len(missing)} dead row(s)")
        for lineno, cell in missing:
            print(f"  docs/testing.md:{lineno}  {cell}")
        return 1

    print(
        f"docs/testing.md per-module map: {checked} module rows, "
        "all resolve against git ls-files"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
