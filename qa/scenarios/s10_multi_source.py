"""Scenario 10 — Multi-source priority + cross-source dedup.

Required sources: qa/sandbox/multi-source-a, qa/sandbox/multi-source-b
Probes: EXACT_DUPLICATE across sources, near-dup grouping,
source-order priority (multi-source-a is configured FIRST → wins ties).
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s10_multi_source")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    sources = _uia.read_configured_sources(dlg)
    print(f"  configured_sources={sources!r}")
    print(f"  source_order={sources!r}")  # order matters here

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    # For multi-source dedup: which source folder did the Ref come from?
    print("step: analyze_priority")
    for r in rows:
        if r.cells and r.cells[0] == "Ref":
            ref_folder = r.cells[2] if len(r.cells) > 2 else "?"
            print(f"  ref_folder={ref_folder!r}")

    print("scenario: s10_multi_source DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
