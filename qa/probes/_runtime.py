"""Shared runtime for ``qa/probes/`` modules — launch + scan + close.

Probes are exploratory live-UIA inspectors that complement the scripted
``qa/scenarios/sNN_*.py`` drivers. Where a scenario says "do these steps,
assert the end state matches X", a probe says "inspect the current
state of Y, flag anomalies" — see issue #243.

Each probe under ``qa.probes.*`` is self-runnable via
``python -m qa.probes.<name>``. To keep individual probe modules short,
this runtime module handles the boilerplate: write ``qa/settings.json``,
clear the QA window-state INI (matching ``qa.scenarios.configure``),
launch ``main.py`` as a subprocess, wait for the main window, run a
scan to load a manifest, and tear everything down on exit.

There is intentionally NO batch runner here. v1 is "one probe at a
time on the CLI" per the #243 design comment — adding a registry +
sharding is deferred until we have more probes to justify it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pywinauto.controls.uiawrapper import UIAWrapper

from qa.scenarios import _uia
from qa.scenarios._batch import _close_window, _wait_for_main_window

REPO = Path(__file__).resolve().parents[2]
# Inherit the Python that invoked us — same convention as
# ``qa.scenarios._batch.PY``. Works under .venv on local dev and
# under actions/setup-python in CI.
PY = sys.executable

SETTINGS_PATH = REPO / "qa" / "settings.json"
# Same INI that ``qa.scenarios.configure`` clears before each scenario —
# stale geometry from a previous run breaks right-click-by-screen-coords
# probes if it ever positions the window off-screen. See #141 history.
QA_WINDOW_STATE_INI = REPO / "qa" / "window_state.ini"


def _write_probe_settings(sources: list[str]) -> None:
    """Write ``qa/settings.json`` with ``sources`` as the source list.

    Mirrors ``qa.scenarios._config.build_settings`` — same thumbnail
    cache + output-manifest path so probes see the same QA-sandbox
    layout the scenario batch sees. Inlined rather than imported so
    probes don't need a SCENARIO_SOURCES entry.
    """
    cfg = {
        "_comment": "Auto-written by qa.probes._runtime.",
        "thumbnail_size": 256,
        "thumbnail_mem_cache": 128,
        "thumbnail_disk_cache_dir": "qa/.thumb-cache",
        "sources": {
            "list": [{"path": p, "recursive": True} for p in sources],
            "output": "qa/run-manifest.sqlite",
        },
    }
    SETTINGS_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _terminate(proc: subprocess.Popen) -> None:
    """Wait for ``proc`` to exit, terminating if it gets stuck.

    Matches the close-then-wait pattern from ``qa.scenarios._batch.run_one``
    so probes leave the same residue (none) as a scenario run does.
    """
    try:
        proc.wait(timeout=8)
        return
    except subprocess.TimeoutExpired:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@contextmanager
def app_with_manifest(
    sources: list[str],
    scan_timeout: float = 60,
) -> Iterator[UIAWrapper]:
    """Yield a main-window UIA wrapper with a manifest scanned + loaded.

    Configures ``qa/settings.json`` for ``sources``, launches
    ``main.py``, runs a scan, and clicks Close & Load. The yielded
    window has the result tree populated and is ready for inspection.

    On exit (success or exception) the window is closed via UIA and
    the subprocess is reaped.

    Typical use::

        from qa.probes._runtime import app_with_manifest

        def main() -> int:
            with app_with_manifest(["qa/sandbox/near-duplicates"]) as win:
                # ...inspect win via qa.scenarios._uia helpers...
                return 0
    """
    _write_probe_settings(sources)
    if QA_WINDOW_STATE_INI.exists():
        QA_WINDOW_STATE_INI.unlink()

    env = os.environ.copy()
    env["PHOTO_MANAGER_HOME"] = "qa"
    env["QT_ACCESSIBILITY"] = "1"
    proc = subprocess.Popen(
        [PY, "main.py"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"launched main.py pid={proc.pid}", flush=True)

    try:
        if not _wait_for_main_window(proc.pid, timeout=8.0):
            # Same as ``qa.scenarios._batch.run_one``: don't raise here.
            # First-launch under a cold cache exceeds 8s on this dev
            # workstation regularly. ``connect_main``'s own retry below
            # surfaces a clearer error if the app really failed to start.
            print(
                f"WARN: main window did not appear within 8s for pid={proc.pid}; "
                f"continuing — connect_main will retry.",
                flush=True,
            )

        _, win = _uia.connect_main(timeout=20)
        dlg, _ = _uia.open_scan_dialog(win)
        print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

        log, elapsed = _uia.run_scan_and_wait(dlg, timeout=scan_timeout)
        print(f"  scan_elapsed_s={elapsed:.2f}")
        for line in _uia.extract_summary(log):
            if line:
                print(f"  log: {line}")

        _uia.close_and_load_manifest(dlg)
        # Reconnect — close_and_load_manifest leaves the dialog gone
        # but pywinauto's cached top_window can hold a stale handle.
        _, win = _uia.connect_main()
        # Small grace for the model to populate before the probe walks
        # tree descendants.
        time.sleep(0.5)
        yield win
    finally:
        try:
            _close_window()
        except Exception:
            pass
        _terminate(proc)
