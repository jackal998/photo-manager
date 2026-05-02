"""Scenario 1 — Happy path: scan + review + mark.

Required sources (write before launching): qa/sandbox/huge,
qa/sandbox/near-duplicates, qa/sandbox/unique
PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import ctypes
import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s01_happy_path")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  scan_dialog_hwnd={scan_hwnd}")

    print("step: read_scan_dialog")
    output_path = dlg.child_window(
        auto_id=_uia.SCAN_AID_OUTPUT_PATH, control_type="Edit"
    ).window_text()
    spinners = [s.window_text() for s in dlg.descendants(control_type="Spinner")]
    print(f"  output_path={output_path!r}")
    print(f"  spinner_values={spinners!r}")
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    remaining = [t for _, _, t in _uia.list_process_windows(win.process_id())]
    print(f"  windows_after_close={remaining!r}")

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    print("step: verify_action_menu_enabled")
    popup = _uia.open_menu(win, _uia.MENU_ACTION)
    for it in popup.descendants(control_type="MenuItem"):
        try:
            print(f"  action_menu: title={it.window_text()!r} enabled={it.is_enabled()}")
        except Exception as e:
            print(f"  action_menu: err={e!r}")
    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)

    print("scenario: s01_happy_path DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
