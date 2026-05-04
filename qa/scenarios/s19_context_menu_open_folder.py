"""Scenario 19 — Right-click context menu → Open Folder (#102).

Required source: qa/sandbox/near-duplicates (5 .jpg fixtures).

Drives the Open Folder action on a result-tree row end-to-end:
  scan → close & load →
  left-click row → right-click row → Open Folder →
  verify a new Windows Explorer window spawned with the fixture folder open →
  close that window cleanly via WM_CLOSE.

Catches drift in:
  - Open Folder action wiring under _create_single_selection_menu
    (app/views/handlers/context_menu.py:93-122)
  - The Windows branch (subprocess.Popen(["explorer", "/select,", path]))
  - Path normalization in `normalize_windows_path`

Distinct from s15 which covers the Set Action submenu of the same context
menu; the two actions branch in different parts of
``_create_single_selection_menu`` so s15 doesn't catch Open Folder drift.

Verification approach: Win32 ``EnumWindows`` snapshot before/after the
click, filtered to class ``CabinetWClass`` (Explorer's folder-window
class). Looking for a NEW window with the fixture folder name in title
is robust to whatever Explorer windows the user already has open.

Cleanup: the spawned Explorer window is dismissed via
``PostMessageW(hwnd, WM_CLOSE, 0, 0)`` so the test doesn't leak windows
into the user's session. taskkill on explorer.exe would nuke the whole
shell — desktop, taskbar, tray — so we never use it here.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

ROW_TARGET = "neardup_00_q95.jpg"
EXPECTED_FOLDER_NAME = "near-duplicates"


def main() -> int:
    print("scenario: s19_context_menu_open_folder")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Set up a manifest so the result tree has rows to right-click ──────
    print("step: scan_and_load")
    dlg, _ = _uia.open_scan_dialog(win)
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: scan did not produce manifest at {MANIFEST_PATH}")
        return 1
    _, win = _uia.connect_main()

    # ── Snapshot existing Explorer windows before the click ───────────────
    print("step: snapshot_explorer_baseline")
    baseline = _uia.list_explorer_windows()
    baseline_hwnds = {hwnd for hwnd, _ in baseline}
    print(f"  baseline_explorer_count={len(baseline)}")

    # ── Right-click target row, navigate to Open Folder ───────────────────
    print(f"step: right_click_row {ROW_TARGET!r}")
    _uia.left_click_tree_row(win, ROW_TARGET)
    _uia.right_click_tree_row(win, ROW_TARGET)
    _uia.select_popup_menu_path(pid, [_uia.CTX_OPEN_FOLDER])

    # ── Wait for Explorer to spawn, find the new window ───────────────────
    print("step: wait_for_explorer_window")
    new_hwnds: list[tuple[int, str]] = []
    deadline = time.time() + 5
    while time.time() < deadline:
        current = _uia.list_explorer_windows()
        new_hwnds = [(h, t) for h, t in current if h not in baseline_hwnds]
        if new_hwnds:
            break
        time.sleep(0.2)
    print(f"  new_explorer_windows={[(h, t) for h, t in new_hwnds]!r}")

    if not new_hwnds:
        print(
            "FAIL: no new Explorer window appeared within 5s after Open Folder "
            "(regression of #102 — Open Folder action wiring or subprocess "
            "invocation broken)"
        )
        return 1

    # The window title is the Explorer-displayed folder name, which on
    # Windows defaults to the folder's basename. Match case-insensitively
    # and as a substring (some locales prefix with parent path).
    matching = [
        (h, t) for h, t in new_hwnds
        if EXPECTED_FOLDER_NAME.lower() in (t or "").lower()
    ]
    print(f"  matching_by_folder_name={[(h, t) for h, t in matching]!r}")

    if not matching:
        # New Explorer windows appeared but none had near-duplicates in
        # the title. Could be: Explorer opened a different folder than
        # expected (path normalization regression), or the locale labels
        # differently — investigate before failing hard. Close whatever
        # we spawned, then fail.
        for hwnd, _t in new_hwnds:
            _uia.close_window_by_hwnd(hwnd)
        print(
            f"FAIL: spawned Explorer window did not show "
            f"{EXPECTED_FOLDER_NAME!r} (titles were "
            f"{[t for _, t in new_hwnds]!r})"
        )
        return 1

    # ── Cleanup: close the spawned window via WM_CLOSE ────────────────────
    print("step: close_spawned_explorer")
    for hwnd, _t in matching:
        _uia.close_window_by_hwnd(hwnd)

    # Verify it actually closed (PostMessage is async; give it a beat).
    time.sleep(0.5)
    after = _uia.list_explorer_windows()
    after_hwnds = {hwnd for hwnd, _ in after}
    leaked = [(h, t) for h, t in matching if h in after_hwnds]
    if leaked:
        # Soft-warn: the window didn't honor WM_CLOSE in time. Not a
        # test failure (the action under test fired correctly), but
        # the operator will see an Explorer window still open.
        print(f"WARN: spawned Explorer windows did not close: {leaked!r}")
    else:
        print("  cleanup ok: spawned Explorer window closed")

    print("scenario: s19_context_menu_open_folder DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
