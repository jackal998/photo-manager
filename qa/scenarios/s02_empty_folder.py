"""Scenario 2 — Empty folder: empty-state UX, no-results dialog.

Required sources: qa/sandbox/empty
Probes: how the app handles a scan that finds zero files.
"""
from __future__ import annotations

import sys
import time

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s02_empty_folder")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=20)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in log.splitlines():
        s = line.strip()
        if s:
            print(f"  log: {s[:160]}")

    print("step: post_scan_dialog_state")
    pid = win.process_id()
    wins = [t for _, _, t in _uia.list_process_windows(pid)]
    print(f"  open_windows={wins!r}")
    # Did anything pop up? (empty-state dialog, message, etc.)
    extra = [t for t in wins if t not in ("Photo Manager - M1", "Scan Sources")]
    print(f"  extra_dialogs={extra!r}")

    print("step: close_dialog")
    try:
        _uia.close_and_load_manifest(dlg)
    except Exception as e:
        print(f"  close_load_failed={e!r}")
        # Fall back to title-bar close
        _uia.cancel_scan_dialog(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows[:10]:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    print("scenario: s02_empty_folder DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
