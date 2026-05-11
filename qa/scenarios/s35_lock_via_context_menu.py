"""Scenario 35 — main-window right-click Lock / Unlock (#182 follow-up).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Closes the layer-3 coverage gap that #175 originally left and #182's
fix-up exposed: the main-window context menu's **standalone Lock /
Unlock items** (not the regex flow — that's s32). Bridge layer-1
tests in tests/test_context_menu.py::TestActionHandlersImplBridge
pin the delegation, but until this scenario landed there was no
end-to-end verification that a single-row or multi-select right-click
→ Lock actually flips is_locked in sqlite.

Drives the full per-row + multi-row Lock/Unlock cycle:
  scan → close & load →
  (a) left-click row 0 (q95) → right-click → Lock →
      verify only row 0 has is_locked=1;
  (b) left-click row 0 (q95) → right-click → Unlock →
      verify is_locked back to 0 (idempotent path);
  (c) left-click row 1 (q88), Ctrl+click row 2 (q80) → right-click row 2 →
      Lock → verify both rows now have is_locked=1;
  (d) same multi-selection → right-click → Unlock →
      verify both rows back to is_locked=0.

Catches drift in: ContextMenuHandler's Lock/Unlock wiring,
ActionHandlersImpl.set_locked_state proxy (the missing-proxy bug
#182 exposed), set_locked_state's in-memory mutation + sqlite write
path, and the "is_locked is orthogonal to user_decision" invariant.

Sister to s15 (decision via right-click) and s32 (lock via regex
flow). Same fixture; verification reads is_locked from manifest.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

ROW_SINGLE = "neardup_00_q95.jpg"
ROW_MULTI_A = "neardup_01_q88.jpg"
ROW_MULTI_B = "neardup_02_q80.jpg"
ROW_UNTOUCHED_A = "neardup_03_q72.jpg"
ROW_UNTOUCHED_B = "neardup_04_q65.jpg"


def _read_lock_state() -> dict[str, bool]:
    """Return {basename: is_locked} for every fixture row in the manifest."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, is_locked FROM migration_manifest "
            "WHERE source_path LIKE ?",
            ("%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: bool(loc) for p, loc in rows}


def _print_state(label: str, state: dict[str, bool]) -> None:
    for name in sorted(state):
        glyph = "🔒" if state[name] else "  "
        print(f"  {label}  {glyph} {name}")


def _assert_locked(
    state: dict[str, bool],
    expected_locked: set[str],
    step: str,
) -> str | None:
    """Return error message if any row deviates from ``expected_locked``."""
    for name, locked in state.items():
        want = name in expected_locked
        if locked != want:
            return (
                f"{step}: {name}: expected is_locked={want}, got "
                f"is_locked={locked}"
            )
    return None


def main() -> int:
    print("scenario: s35_lock_via_context_menu")
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

    print("step: snapshot_initial")
    initial = _read_lock_state()
    _print_state("init", initial)
    if any(initial.values()):
        print("FAIL: fresh manifest has unexpected locked rows")
        return 1

    failures: list[str] = []

    # ── (a) Single-row right-click → Lock ─────────────────────────────────
    print(f"step: single_row_lock target={ROW_SINGLE!r}")
    _uia.left_click_tree_row(win, ROW_SINGLE)
    _uia.right_click_tree_row(win, ROW_SINGLE)
    _uia.select_popup_menu_path(pid, [_uia.CTX_LOCK])
    after_a = _read_lock_state()
    _print_state("a   ", after_a)
    err = _assert_locked(after_a, expected_locked={ROW_SINGLE}, step="single_lock")
    if err:
        failures.append(err)

    # ── (b) Single-row right-click → Unlock ───────────────────────────────
    print(f"step: single_row_unlock target={ROW_SINGLE!r}")
    _uia.left_click_tree_row(win, ROW_SINGLE)
    _uia.right_click_tree_row(win, ROW_SINGLE)
    _uia.select_popup_menu_path(pid, [_uia.CTX_UNLOCK])
    after_b = _read_lock_state()
    _print_state("b   ", after_b)
    err = _assert_locked(after_b, expected_locked=set(), step="single_unlock")
    if err:
        failures.append(err)

    # ── (c) Multi-row Lock via Ctrl+click ─────────────────────────────────
    print(f"step: multi_row_lock targets=[{ROW_MULTI_A!r}, {ROW_MULTI_B!r}]")
    _uia.left_click_tree_row(win, ROW_MULTI_A)
    _uia.ctrl_click_tree_row(win, ROW_MULTI_B)
    _uia.right_click_tree_row(win, ROW_MULTI_B)
    _uia.select_popup_menu_path(pid, [_uia.CTX_LOCK])
    after_c = _read_lock_state()
    _print_state("c   ", after_c)
    err = _assert_locked(
        after_c, expected_locked={ROW_MULTI_A, ROW_MULTI_B}, step="multi_lock"
    )
    if err:
        failures.append(err)

    # ── (d) Multi-row Unlock on the same selection ───────────────────────
    print(f"step: multi_row_unlock targets=[{ROW_MULTI_A!r}, {ROW_MULTI_B!r}]")
    _uia.left_click_tree_row(win, ROW_MULTI_A)
    _uia.ctrl_click_tree_row(win, ROW_MULTI_B)
    _uia.right_click_tree_row(win, ROW_MULTI_B)
    _uia.select_popup_menu_path(pid, [_uia.CTX_UNLOCK])
    after_d = _read_lock_state()
    _print_state("d   ", after_d)
    err = _assert_locked(after_d, expected_locked=set(), step="multi_unlock")
    if err:
        failures.append(err)

    # Untouched rows must stay unchanged across every step.
    for name in (ROW_UNTOUCHED_A, ROW_UNTOUCHED_B):
        if after_d[name] is not False:
            failures.append(
                f"untouched row {name} unexpectedly locked={after_d[name]}"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s35_lock_via_context_menu DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
