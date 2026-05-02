"""CLI: write qa/settings.json for a scenario.

Usage:
    .venv/Scripts/python.exe -m qa.scenarios.configure <scenario_name>

Run this BEFORE launching main.py for the scenario.
"""
from __future__ import annotations

import sys

from qa.scenarios._config import SCENARIO_SOURCES, write_settings


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: python -m qa.scenarios.configure <scenario>")
        print(f"known scenarios: {sorted(SCENARIO_SOURCES)}")
        return 2
    name = sys.argv[1]
    try:
        path = write_settings(name)
    except KeyError as e:
        print(f"error: {e}")
        return 1
    print(f"wrote {path}")
    print(f"sources={SCENARIO_SOURCES[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
