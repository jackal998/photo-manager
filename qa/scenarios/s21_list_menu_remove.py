"""Scenario 21 — List menu → Remove from List (menu-bar path).

Required source: qa/sandbox/near-duplicates (5 files, basenames
neardup_NN_qXX.jpg).

Drives the menu-bar entry routed through
MainWindow._remove_from_list_toolbar →
file_operations.remove_from_list_toolbar — three branches that s01
currently only probes for enabled-state, never invokes:

  (a) No selection: List → Remove from List → "Remove from List"
      QMessageBox carrying "No items selected" body. Manifest
      unchanged.
  (b) Single highlight: left-click one row → List → Remove from List
      → status bar "Removed 1 item from list", that row marked
      'removed' in the manifest, others unchanged.
  (c) Multi highlight: left-click + Ctrl+click → List → Remove from
      List → status bar "Removed 2 items from list", both rows
      marked 'removed', untouched rows unchanged.

Distinct from s20 (right-click multi-row branch through
remove_items_from_list) and from s15 (Set Action via context menu).
This scenario covers remove_from_list_toolbar specifically.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

ROW_SINGLE = "neardup_00_q95.jpg"
ROW_MULTI_A = "neardup_01_q88.jpg"
ROW_MULTI_B = "neardup_02_q80.jpg"
ROWS_UNTOUCHED = ("neardup_03_q72.jpg", "neardup_04_q65.jpg")
ALL_ROWS = (ROW_SINGLE, ROW_MULTI_A, ROW_MULTI_B, *ROWS_UNTOUCHED)


def _read_decisions() -> dict[str, str]:
    """Return {basename: user_decision} for every fixture row in the manifest."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision FROM migration_manifest "
            "WHERE source_path LIKE ?",
            ("%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (d or "") for p, d in rows}


def main() -> int:
    print("scenario: s21_list_menu_remove")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Setup: scan + close & load ────────────────────────────────────────
    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: snapshot_pre_decisions")
    pre = _read_decisions()
    if set(pre) != set(ALL_ROWS):
        print(
            f"FAIL: fixture row mismatch; pre={sorted(pre)} "
            f"expected={sorted(ALL_ROWS)}"
        )
        return 1
    print(f"  pre_decisions={dict(sorted(pre.items()))}")

    failures: list[str] = []

    # ── (a) No-selection branch ───────────────────────────────────────────
    print("step: branch_a_no_selection")
    _uia.menu_path(win, _uia.MENU_LIST, "Remove from List")
    try:
        msgbox_hwnd = _uia.wait_for_dialog(pid, "Remove from List", timeout=5)
    except TimeoutError:
        print(
            "FAIL: no-selection branch did not surface "
            "'Remove from List' QMessageBox"
        )
        return 1
    msgbox = _uia.connect_by_handle(msgbox_hwnd)
    body_seen = False
    for label in msgbox.descendants(control_type="Text"):
        try:
            t = (label.window_text() or "").strip()
            if "No items selected" in t:
                body_seen = True
                print(f"  body_text={t!r}")
                break
        except Exception:
            continue
    if not body_seen:
        failures.append(
            "no-selection QMessageBox missing 'No items selected' body"
        )
    if not _uia.dismiss_dialog_by_title(pid, "Remove from List", timeout=3):
        failures.append(
            "could not dismiss no-selection 'Remove from List' QMessageBox"
        )
    post_a = _read_decisions()
    if post_a != pre:
        failures.append(
            f"manifest mutated by no-selection branch: pre={pre} post={post_a}"
        )

    # ── (b) Single-row highlight branch ───────────────────────────────────
    print(f"step: branch_b_single target={ROW_SINGLE!r}")
    _, win = _uia.connect_main()
    _uia.left_click_tree_row(win, ROW_SINGLE)
    _uia.menu_path(win, _uia.MENU_LIST, "Remove from List")
    inv_b = _invariants.assert_status_bar_matches(
        win, r"Removed 1 item from list", within_s=2.5
    )
    if not inv_b:
        failures.append("status bar did not echo 'Removed 1 item from list'")
    post_b = _read_decisions()
    print(f"  post_b={dict(sorted(post_b.items()))}")
    if post_b.get(ROW_SINGLE) != "removed":
        failures.append(
            f"single branch: {ROW_SINGLE} user_decision="
            f"{post_b.get(ROW_SINGLE)!r}, expected 'removed'"
        )
    for other in ALL_ROWS:
        if other == ROW_SINGLE:
            continue
        if post_b.get(other) != pre.get(other):
            failures.append(
                f"single branch leaked into {other}: "
                f"pre={pre.get(other)!r} post={post_b.get(other)!r}"
            )

    # ── (c) Multi-row highlight branch ────────────────────────────────────
    print(f"step: branch_c_multi targets=[{ROW_MULTI_A!r}, {ROW_MULTI_B!r}]")
    _, win = _uia.connect_main()
    _uia.left_click_tree_row(win, ROW_MULTI_A)
    _uia.ctrl_click_tree_row(win, ROW_MULTI_B)
    _uia.menu_path(win, _uia.MENU_LIST, "Remove from List")
    inv_c = _invariants.assert_status_bar_matches(
        win, r"Removed 2 items from list", within_s=2.5
    )
    if not inv_c:
        failures.append("status bar did not echo 'Removed 2 items from list'")
    post_c = _read_decisions()
    print(f"  post_c={dict(sorted(post_c.items()))}")
    if post_c.get(ROW_MULTI_A) != "removed":
        failures.append(
            f"multi branch: {ROW_MULTI_A} user_decision="
            f"{post_c.get(ROW_MULTI_A)!r}, expected 'removed'"
        )
    if post_c.get(ROW_MULTI_B) != "removed":
        failures.append(
            f"multi branch: {ROW_MULTI_B} user_decision="
            f"{post_c.get(ROW_MULTI_B)!r}, expected 'removed'"
        )
    for other in ROWS_UNTOUCHED:
        if post_c.get(other) != pre.get(other):
            failures.append(
                f"untouched {other}: pre={pre.get(other)!r} "
                f"post={post_c.get(other)!r}"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: invariant_actions_enabled")
    inv_actions = _invariants.assert_manifest_actions_consistent(
        win, expected_enabled=True
    )
    if not inv_actions:
        print(
            "FAIL: manifest-gated menu items not all enabled after "
            "Remove from List"
        )
        return 1

    print("scenario: s21_list_menu_remove DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
