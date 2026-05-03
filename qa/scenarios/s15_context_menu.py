"""Scenario 15 — Right-click context menu Set Action → delete / keep.

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Drives the per-row context-menu decision flow end-to-end:
  scan → close & load →
  (a) left-click row 0 (q95) → right-click → Set Action → delete →
      verify only row 0 has user_decision='delete' in manifest;
  (b) left-click row 1 (q88) → right-click → Set Action → keep (remove action) →
      verify row 1's user_decision is empty (the "keep" stored value);
  (c) left-click row 2 (q80), Ctrl+click row 3 (q72) → right-click row 3 →
      Set Action → delete → verify both rows now have user_decision='delete'.

Catches drift in: tree QTreeView → ContextMenuHandler wiring; "Set Action"
submenu structure and labels; `set_decision()` write path on single and
multi-row selection; QAbstractItemView's right-click no-auto-select
default (the prior left-click is load-bearing — see context_menu.py:67–69).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

ROW_DELETE_SINGLE = "neardup_00_q95.jpg"
ROW_KEEP_SINGLE = "neardup_01_q88.jpg"
ROW_MULTI_A = "neardup_02_q80.jpg"
ROW_MULTI_B = "neardup_03_q72.jpg"
ROW_UNTOUCHED = "neardup_04_q65.jpg"


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


def _assert(name: str, expected: str, actual: str) -> str | None:
    """Return error message if mismatch, else None."""
    if actual != expected:
        return f"{name}: expected user_decision={expected!r}, got {actual!r}"
    return None


def main() -> int:
    print("scenario: s15_context_menu")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: snapshot_pre_decisions")
    pre = _read_decisions()
    expected_rows = {ROW_DELETE_SINGLE, ROW_KEEP_SINGLE, ROW_MULTI_A,
                     ROW_MULTI_B, ROW_UNTOUCHED}
    if set(pre) != expected_rows:
        print(f"FAIL: fixture row mismatch; pre={sorted(pre)} "
              f"expected={sorted(expected_rows)}")
        return 1
    print(f"  pre={dict(sorted(pre.items()))}")

    failures: list[str] = []

    # ── (a) Single-row delete via right-click ─────────────────────────────
    print("step: single_row_delete")
    print(f"  target={ROW_DELETE_SINGLE!r}")
    _uia.left_click_tree_row(win, ROW_DELETE_SINGLE)
    _uia.right_click_tree_row(win, ROW_DELETE_SINGLE)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])
    post_a = _read_decisions()
    print(f"  post_a={dict(sorted(post_a.items()))}")
    err = _assert(ROW_DELETE_SINGLE, "delete", post_a[ROW_DELETE_SINGLE])
    if err:
        failures.append(f"single_delete: {err}")
    for other in expected_rows - {ROW_DELETE_SINGLE}:
        err = _assert(other, pre[other], post_a[other])
        if err:
            failures.append(f"single_delete leaked into {err}")

    # ── (b) Single-row keep (clears any prior decision) ───────────────────
    print("step: single_row_keep")
    print(f"  target={ROW_KEEP_SINGLE!r}")
    _uia.left_click_tree_row(win, ROW_KEEP_SINGLE)
    _uia.right_click_tree_row(win, ROW_KEEP_SINGLE)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_KEEP])
    post_b = _read_decisions()
    print(f"  post_b={dict(sorted(post_b.items()))}")
    err = _assert(ROW_KEEP_SINGLE, "", post_b[ROW_KEEP_SINGLE])
    if err:
        failures.append(f"single_keep: {err}")
    # ROW_DELETE_SINGLE should still be 'delete' from step (a).
    err = _assert(ROW_DELETE_SINGLE, "delete", post_b[ROW_DELETE_SINGLE])
    if err:
        failures.append(f"single_keep clobbered prior delete: {err}")

    # ── (c) Multi-row delete via Ctrl+click ───────────────────────────────
    print("step: multi_row_delete")
    print(f"  targets=[{ROW_MULTI_A!r}, {ROW_MULTI_B!r}]")
    _uia.left_click_tree_row(win, ROW_MULTI_A)
    _uia.ctrl_click_tree_row(win, ROW_MULTI_B)
    _uia.right_click_tree_row(win, ROW_MULTI_B)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])
    post_c = _read_decisions()
    print(f"  post_c={dict(sorted(post_c.items()))}")
    err = _assert(ROW_MULTI_A, "delete", post_c[ROW_MULTI_A])
    if err:
        failures.append(f"multi_delete A: {err}")
    err = _assert(ROW_MULTI_B, "delete", post_c[ROW_MULTI_B])
    if err:
        failures.append(f"multi_delete B: {err}")
    # ROW_UNTOUCHED must remain at its pre value across all three steps.
    err = _assert(ROW_UNTOUCHED, pre[ROW_UNTOUCHED], post_c[ROW_UNTOUCHED])
    if err:
        failures.append(f"untouched row mutated: {err}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s15_context_menu DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
