"""Scenario 4 — Corrupted file handling.

Required sources: qa/sandbox/corrupted
Probes: hash/EXIF error paths — does the scan tolerate a corrupted file,
log a meaningful error, and continue?

Catches drift in: post-#87 summary reconciliation. The fixture has 1
truncated JPEG which decodes to phash=None and is excluded from the
manifest. The summary must reconcile:
  - "Indexed in manifest :       0"
  - "Skipped (unreadable):       1"
A regression that brings back the misleading "Total files scanned: 0"
without a Skipped line gets caught here.
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s04_corrupted")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    # Print every log line — corruption findings live in the log text
    for line in log.splitlines():
        s = line.strip()
        if s:
            print(f"  log: {s[:200]}")
    # Surface error / warning lines
    err_lines = [l for l in log.splitlines() if any(
        k in l.lower() for k in ("error", "warning", "fail", "skip", "corrupt")
    )]
    print(f"  error_keyword_lines={len(err_lines)}")

    # #87: the summary must reconcile with the per-step counts above.
    print("step: assert_summary_reconciles")
    if "Indexed in manifest" not in log:
        print("FAIL: summary missing 'Indexed in manifest' headline (regression of #87)")
        return 1
    if "Total files scanned" in log:
        print("FAIL: summary still uses old misleading 'Total files scanned' label (#87)")
        return 1
    if "Skipped (unreadable)" not in log:
        print(
            "FAIL: corrupt-only scan must surface a 'Skipped (unreadable)' "
            "line so the headline 0 reconciles with 'Hashed 1/1' (#87)"
        )
        return 1

    print("step: close_dialog")
    try:
        _uia.close_and_load_manifest(dlg)
    except Exception as e:
        print(f"  close_load_failed={e!r}")
        _uia.cancel_scan_dialog(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows[:15]:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    print("scenario: s04_corrupted DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
