"""Scenario 1 — Happy path: scan + review + mark.

Required sources (write before launching): qa/sandbox/huge,
qa/sandbox/near-duplicates, qa/sandbox/unique
PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py

Also probes the three "post-load UI state" behaviors that earlier batch
runs couldn't verify:

  - #42 — first-run hint label visible at startup, hidden after a
    manifest loads.
  - #52 — "Remove from List" menu item disabled pre-manifest,
    enabled after.
  - #58 — status bar shows "Loaded manifest: N group(s), M isolated
    file(s)" for the just-loaded manifest.
"""
from __future__ import annotations

import ctypes
import sys

from qa.scenarios import _uia


def main() -> int:
    print("scenario: s01_happy_path")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    # --- Pre-scan state probes (gaps closed by this scenario) -----------
    print("step: probe_first_run_state")
    pre = _uia.read_main_window_state(win)
    print(f"  empty_state_visible={pre['empty_state_visible']}")
    print(f"  tree_visible={pre['tree_visible']}")
    print(f"  status_bar_text={pre['status_bar_text']!r}")

    print("step: probe_list_menu_pre_load")
    for title, enabled in _uia.probe_menu_items(win, _uia.MENU_LIST):
        print(f"  list_menu: title={title!r} enabled={enabled}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  scan_dialog_hwnd={scan_hwnd}")

    print("step: read_scan_dialog")
    output_path = dlg.child_window(
        auto_id=_uia.SCAN_AID_OUTPUT_PATH, control_type="Edit"
    ).window_text()
    spinners = [s.window_text() for s in dlg.descendants(control_type="Spinner")]
    print(f"  output_path={output_path!r}")
    print(f"  spinner_values={spinners!r}")
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    remaining = [t for _, _, t in _uia.list_process_windows(win.process_id())]
    print(f"  windows_after_close={remaining!r}")

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    # --- Post-load state probes (verify the transitions fired) ----------
    print("step: probe_post_load_state")
    post = _uia.read_main_window_state(win)
    print(f"  empty_state_visible={post['empty_state_visible']}")
    print(f"  tree_visible={post['tree_visible']}")
    print(f"  status_bar_text={post['status_bar_text']!r}")

    print("step: probe_list_menu_post_load")
    for title, enabled in _uia.probe_menu_items(win, _uia.MENU_LIST):
        print(f"  list_menu: title={title!r} enabled={enabled}")

    print("step: verify_action_menu_enabled")
    for title, enabled in _uia.probe_menu_items(win, _uia.MENU_ACTION):
        print(f"  action_menu: title={title!r} enabled={enabled}")

    print("scenario: s01_happy_path DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
