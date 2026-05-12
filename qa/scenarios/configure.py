"""CLI: write qa/settings.json for a scenario.

Usage:
    .venv/Scripts/python.exe -m qa.scenarios.configure <scenario_name>

Run this BEFORE launching main.py for the scenario.
"""
from __future__ import annotations

import sys
from pathlib import Path

from qa.scenarios._config import SCENARIO_SOURCES, write_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
# #141 introduced MainWindow geometry persistence via Qt QSettings,
# stored under PHOTO_MANAGER_HOME as ``window_state.ini``. Carrying a
# previous scenario's geometry into the next one re-positions/re-sizes
# the window in ways that break right-click-by-screen-coords scenarios
# (s15/s19/s20/s21/s25/s30/s35) — the click lands outside the visible
# row. Each scenario must start with a clean geometry. s39 itself
# wipes this file at startup before its own two-launch round-trip; this
# wipe at configure-time is what makes EVERY other scenario start fresh.
QA_WINDOW_STATE_INI = REPO_ROOT / "qa" / "window_state.ini"


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
    if QA_WINDOW_STATE_INI.exists():
        QA_WINDOW_STATE_INI.unlink()
        print(f"cleared {QA_WINDOW_STATE_INI}")
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
