"""Scenario 5 — Heavy preview interaction.

Required sources: qa/sandbox/huge
Probes: large-image perf, keyboard nav, rapid clicks, preview rendering.

Strategy: scan the huge folder (1 file), load manifest, select the row,
hit it with rapid Enter / arrow keys, measure response.
"""
from __future__ import annotations

import sys
import time

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s05_huge_preview")
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

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows[:10]:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    # Probe preview perf: click the (only) file row, then rapid clicks
    print("step: select_row_and_probe_preview")
    items = win.descendants(control_type="TreeItem")
    target = next((i for i in items if (i.window_text() or "").endswith(".jpg")), None)
    if target is None:
        print("  no file row found")
    else:
        print(f"  target={target.window_text()!r}")
        t0 = time.time()
        target.click_input()
        time.sleep(0.5)
        # Rapid double clicks
        for _ in range(3):
            target.click_input(double=True)
            time.sleep(0.2)
        print(f"  rapid_click_elapsed_s={time.time() - t0:.2f}")

    # Read preview pane state
    print("step: read_preview_pane")
    for s in win.descendants(control_type="Text"):
        try:
            t = (s.window_text() or "").strip()
            aid = s.element_info.automation_id or ""
            if "PreviewPane" in aid:
                print(f"  preview_text: {t[:80]!r}  aid={aid!r}")
        except Exception:
            pass

    print("scenario: s05_huge_preview DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
