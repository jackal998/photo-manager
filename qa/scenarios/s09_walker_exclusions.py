"""Scenario 9 — Walker exclusion rules.

Required sources: qa/sandbox/walker-exclusions
Probes: only the 2 real photos appear; sidecar.json, Thumbs.db, desktop.ini
correctly skipped.
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s09_walker_exclusions")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    # Check the "→ N files" line — should show 2 if exclusions worked
    for line in log.splitlines():
        if "files" in line.lower() and "→" in line:
            print(f"  walker_count_line: {line.strip()[:200]}")
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
    # Surface filenames seen — these should be the 2 real photos only
    files = []
    for r in rows:
        for c in r.cells:
            if "." in c and "\\" not in c and "/" not in c and len(c) < 60:
                if any(c.lower().endswith(e) for e in (".jpg", ".jpeg", ".png", ".heic", ".tiff", ".webp")):
                    files.append(c)
    print(f"  files_in_results={files}")
    # Excluded files we DON'T want to see
    excluded = ["sidecar.json", "Thumbs.db", "desktop.ini"]
    for ex in excluded:
        present = any(ex in r.cells[1] if len(r.cells) > 1 else False for r in rows)
        print(f"  excluded_file_present: {ex}={present}")

    print("scenario: s09_walker_exclusions DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
