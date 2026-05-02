"""Scenario 7 — Format duplicate (HEIC vs JPG of same scene).

Required sources: qa/sandbox/format-dup
Probes: FORMAT_DUPLICATE classifier — HEIC should win, JPG marked as the dup.
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s07_format_dup")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    # Print the full classifier summary AND any FORMAT_DUPLICATE-tagged lines
    for line in log.splitlines():
        s = line.strip()
        if s and ("FORMAT" in s.upper() or "Manifest Summary" in s
                  or "Group" in s or "%" in s or "──" in s):
            print(f"  log: {s[:200]}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")
    # Check which file was marked as Ref (the winner)
    ref_file = None
    for r in rows:
        if r.cells and r.cells[0] == "Ref":
            ref_file = r.cells[1] if len(r.cells) > 1 else None
            break
    print(f"  ref_file={ref_file!r}")

    print("scenario: s07_format_dup DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
