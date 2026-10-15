"""Check the feature inventory's QA-scenario citations against the drivers on disk.

The inventory is docs/features.md (the landing) plus one entry file per Area in
docs/features/ (#958). Each entry's ``**Related:**`` line cites the layer-3 driver
that covers it. A citation pointing at a file that does not exist is the worst
kind of doc rot: it reads as coverage.

Two assertions:

1. Every ``qa/web/scenarios/...`` citation names a file that exists.
2. Zero ``qa/scenarios/s...`` citations remain — that tree went with the
   desktop client in #646, so any survivor is a stale pointer.

Usage
-----
    python scripts/check_features_scenario_citations.py

Exit 0 when both hold; exit 1 with the offending citations otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FEATURES = REPO / "docs" / "features.md"
FEATURES_DIR = REPO / "docs" / "features"
WEB_SCENARIOS = REPO / "qa" / "web" / "scenarios"

_WEB_CITATION = re.compile(r"qa/web/scenarios/(s[a-z0-9_]+\.py)")
_DEAD_CITATION = re.compile(r"qa/scenarios/s[a-z0-9_]*")


def _sources() -> list[Path]:
    """The landing and every area file, read at call time."""
    return [FEATURES, *sorted(FEATURES_DIR.glob("*.md"))]


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    missing: list[tuple[str, int, str]] = []
    dead: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for source in _sources():
        rel = _rel(source)
        lines = source.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            for name in _WEB_CITATION.findall(line):
                seen.add(name)
                if not (WEB_SCENARIOS / name).is_file():
                    missing.append((rel, lineno, name))
            for hit in _DEAD_CITATION.findall(line):
                dead.append((rel, lineno, hit))

    if missing or dead:
        if missing:
            print(f"features: {len(missing)} citation(s) name a missing driver")
            for rel, lineno, name in missing:
                print(f"  {rel}:{lineno}  qa/web/scenarios/{name}")
        if dead:
            print(f"features: {len(dead)} citation(s) still point at the removed tree")
            for rel, lineno, hit in dead:
                print(f"  {rel}:{lineno}  {hit}")
        return 1

    print(
        f"docs/features (landing + area files): {len(seen)} distinct qa/web/scenarios citations, "
        "all resolve; 0 qa/scenarios/s citations remain"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
