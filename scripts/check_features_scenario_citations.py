"""Check docs/features.md's QA-scenario citations against the drivers on disk.

features.md is the canonical feature inventory, and each entry's
``**Related:**`` line cites the layer-3 driver that covers it. A citation
pointing at a file that does not exist is the worst kind of doc rot: it
reads as coverage.

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
WEB_SCENARIOS = REPO / "qa" / "web" / "scenarios"

_WEB_CITATION = re.compile(r"qa/web/scenarios/(s[a-z0-9_]+\.py)")
_DEAD_CITATION = re.compile(r"qa/scenarios/s[a-z0-9_]*")


def main() -> int:
    text = FEATURES.read_text(encoding="utf-8")
    lines = text.splitlines()

    missing: list[tuple[int, str]] = []
    seen: set[str] = set()
    for lineno, line in enumerate(lines, start=1):
        for name in _WEB_CITATION.findall(line):
            seen.add(name)
            if not (WEB_SCENARIOS / name).is_file():
                missing.append((lineno, name))

    dead: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, start=1):
        for hit in _DEAD_CITATION.findall(line):
            dead.append((lineno, hit))

    if missing or dead:
        if missing:
            print(f"features.md: {len(missing)} citation(s) name a missing driver")
            for lineno, name in missing:
                print(f"  docs/features.md:{lineno}  qa/web/scenarios/{name}")
        if dead:
            print(f"features.md: {len(dead)} citation(s) still point at the removed tree")
            for lineno, hit in dead:
                print(f"  docs/features.md:{lineno}  {hit}")
        return 1

    print(
        f"docs/features.md: {len(seen)} distinct qa/web/scenarios citations, "
        "all resolve; 0 qa/scenarios/s citations remain"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
