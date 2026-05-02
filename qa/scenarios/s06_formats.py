"""Scenario 6 — Multi-format scan: HEIC, PNG, GIF, WebP, TIFF.

Required sources: qa/sandbox/formats
Probes: thumbnails render, dates extracted, GIF graceful handling (no EXIF).
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s06_formats")
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
    # Also surface any per-file warnings about unrecognized formats
    warns = [l for l in log.splitlines() if any(
        k in l.lower() for k in ("warning", "error", "skip", "unrecognized", "no exif")
    )]
    print(f"  warning_lines={len(warns)}")
    for w in warns[:10]:
        print(f"  warn: {w.strip()[:200]}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")
    # Verify each format showed up
    extensions_seen = set()
    for r in rows:
        for cell in r.cells:
            if "." in cell and len(cell) < 80:
                ext = cell.rsplit(".", 1)[-1].lower()
                if len(ext) <= 5:
                    extensions_seen.add(ext)
    print(f"  extensions_in_results={sorted(extensions_seen)}")

    print("scenario: s06_formats DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
