"""Scenario 8 — EXIF edge cases.

Required sources: qa/sandbox/exif-edge
Probes: Date column for: timezone offset, sub-second, CreateDate-only,
DateTime tag-only, zero sentinel, dash sentinel.
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s08_exif_edge")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")
    # UNDATED count is meaningful for this scenario
    for line in log.splitlines():
        if "UNDATED" in line.upper():
            print(f"  log: {line.strip()[:200]}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    # For each file row, surface filename + Shot Date column. Walk all so the
    # LLM can audit per-file what the Date column is showing for each edge case.
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    print("scenario: s08_exif_edge DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
