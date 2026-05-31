"""Scenario 62 — hash-pool re-calibrate checkbox, end-to-end (#486-PR3c).

Required source: ``qa/sandbox/near-duplicates`` (5 JPEGs). The fixture is
deliberately small: the auto-calibration floor is 24 files, so a 5-file
scan exercises the recalibrate → auto → "calibration skipped → thread"
path WITHOUT spawning a real ProcessPoolExecutor in the qa-batch
subprocess (which would be slow and flaky). The point of this driver is
the *checkbox UI + auto-uncheck flow*, not the timing math (that's pinned
at layer 1 in ``tests/test_scan_worker.py::TestHashPoolCalibration``).

What this exercises that layer-1 can't reach:

  1. The "Re-calibrate hash pool on next scan" checkbox renders in the
     Advanced-Settings section with the right label and defaults OFF.
  2. Toggling it ON and running a scan drives the PR3c state machine:
     ``_resolve_hash_pool`` forces ``scan.hash_pool="auto"``, runs the
     calibration path (skipped here → thread on the 5-file sample), and
     **auto-unchecks the box** — the user-visible one-shot behaviour.
  3. The scan still completes and writes a manifest.

The modal path (unchecked + auto + cache miss) is NOT driven here — it's
unit-tested in ``TestResolveHashPool`` (a live QMessageBox in the batch
would risk the modal-dismissal flakiness this repo has hit before).

Batch isolation: ``qa.scenarios.configure`` rewrites qa/settings.json
fresh for the next scenario (no ``scan.hash_pool`` key), so the "auto"
this scenario persists does not leak into later batch scenarios.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# UIA accessible names — must match translations/en.yml.
RECALIBRATE_CHECKBOX_TITLE = "Re-calibrate hash pool on next scan"
ADVANCED_GROUP_TITLE = "Advanced settings"


def _click_advanced_group(dlg) -> None:
    """Expand the Advanced Settings collapsible groupbox (mirrors s49)."""
    grp = dlg.child_window(title=ADVANCED_GROUP_TITLE, control_type="CheckBox")
    grp.toggle()
    time.sleep(0.2)


def _recalibrate_checkbox(dlg):
    return dlg.child_window(
        title=RECALIBRATE_CHECKBOX_TITLE, control_type="CheckBox"
    )


def _is_checked(dlg) -> bool:
    # get_toggle_state: 0 (off) / 1 (on) / 2 (indeterminate).
    return _recalibrate_checkbox(dlg).get_toggle_state() == 1


def main() -> int:
    print("scenario: s62_scan_hash_pool_recalibrate")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    failures: list[str] = []

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: expand_advanced_settings")
    _click_advanced_group(dlg)

    print("step: assert_checkbox_present_and_default_off")
    try:
        checked = _is_checked(dlg)
    except Exception as exc:
        print(
            f"FAIL: re-calibrate checkbox not found by title "
            f"{RECALIBRATE_CHECKBOX_TITLE!r} ({exc!r}) — label drift vs "
            f"translations/en.yml scan_dialog.recalibrate_label?"
        )
        return 1
    if checked:
        print("FAIL: re-calibrate checkbox starts ON; default must be OFF")
        return 1

    print("step: toggle_recalibrate_on")
    _recalibrate_checkbox(dlg).toggle()
    time.sleep(0.2)
    if not _is_checked(dlg):
        print("FAIL: toggling did not turn the re-calibrate checkbox on")
        return 1

    if MANIFEST_PATH.exists():
        try:
            MANIFEST_PATH.unlink()
        except OSError:
            pass

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    # The recalibrate path forces "auto", which runs the calibration code
    # (skipped here → thread on the 5-file sample). Its log line proves the
    # path fired rather than silently falling through to plain thread.
    if "Hash-pool calibration" not in log:
        failures.append(
            "scan log missing a 'Hash-pool calibration' line — the "
            "recalibrate → auto path did not run"
        )

    print("step: assert_checkbox_auto_unchecked")
    if _is_checked(dlg):
        failures.append(
            "re-calibrate checkbox still ON after scan — the one-shot "
            "trigger must auto-uncheck itself (see _resolve_hash_pool)"
        )

    time.sleep(1.0)  # let ScanWorker.finished dispatch (mirrors s49)

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        failures.append(f"scan did not produce manifest at {MANIFEST_PATH}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s62_scan_hash_pool_recalibrate DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
