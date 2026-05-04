"""Scenario 2 — Empty folder: empty-state UX, no-results dialog.

Required sources: qa/sandbox/empty
Probes: how the app handles a scan that finds zero files.

Catches drift in: post-empty-scan focus cue (#86 — Close button must
receive focus so the user has a visible exit), and the empty-state log
line shape ("Done. No media files found — nothing to scan.").
"""
from __future__ import annotations

import sys

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
    extra = [t for t in wins if t not in ("Photo Manager", "Scan Sources")]
    print(f"  extra_dialogs={extra!r}")
    if extra:
        print(f"FAIL: unexpected dialogs after empty scan: {extra!r}")
        return 1

    # #86 — Close is the canonical exit on the empty path; the source-side
    # fix routes focus there so the user has a visible cue. Assert it.
    print("step: assert_close_button_focused")
    focused = _uia.focused_button_name(dlg)
    print(f"  focused_button={focused!r}")
    if focused != "Close":
        print(
            f"FAIL: expected 'Close' button to have focus after empty scan, "
            f"got {focused!r} (regression of #86)"
        )
        return 1

    print("step: close_dialog")
    _uia.close_scan_dialog_via_close_button(dlg)

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
