"""Scenario 3 — Cancel scan mid-run.

Required sources: qa/sandbox/near-duplicates, huge, unique
Probes: interrupt handling — does cancel actually stop, is cleanup clean,
is the manifest left in a consistent state?

Strategy: kick off a scan, immediately click the title-bar Close (×) — there's
no explicit "Cancel" button on the dialog, so the close gesture IS the cancel.
"""
from __future__ import annotations

import sys
import time

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s03_cancel_scan")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: start_then_cancel")
    start_btn = dlg.child_window(title=_uia.SCAN_BTN_START, control_type="Button")
    log_edit = dlg.child_window(auto_id=_uia.SCAN_AID_LOG, control_type="Edit")
    t0 = time.time()
    start_btn.invoke()
    # Let it get into the hashing phase, then cancel
    time.sleep(0.8)
    pre_log = log_edit.window_text() or ""
    print(f"  log_at_cancel={pre_log[-300:]!r}")
    _uia.cancel_scan_dialog(dlg)
    elapsed = time.time() - t0
    print(f"  cancel_elapsed_s={elapsed:.2f}")
    time.sleep(1.0)

    print("step: post_cancel_state")
    pid = win.process_id()
    wins = [t for _, _, t in _uia.list_process_windows(pid)]
    print(f"  open_windows={wins!r}")

    # Inspect main window for stale state
    print("step: read_main_after_cancel")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows_after_cancel={len(rows)}")
    for r in rows[:10]:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    print("scenario: s03_cancel_scan DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
