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
    sources = SCENARIO_SOURCES[name]
    if sources is None:
        # Preserve sentinel — settings.json was NOT rewritten. The scenario
        # reads back what a previous scenario in the batch wrote via the GUI.
        print(f"preserved settings at {path}")
        print("sources=<preserved from previous scenario>")
    else:
        print(f"wrote {path}")
        print(f"sources={sources}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
