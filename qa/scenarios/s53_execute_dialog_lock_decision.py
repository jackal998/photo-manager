"""Scenario 53 — Execute Action dialog: right-click → Lock / Unlock / decision.

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Closes part of the layer-3 coverage gap that #322 left open under
``[qa-not-needed]``: the **non-regex** decision-changing paths inside
``ExecuteActionDialog._on_tree_context_menu``. PR #322 plumbed
``status_reporter`` through the dialog and emits "Decision set" /
"Locked …" / "Unlocked …" on five paths, with L1 tests in
``TestExecuteDialogStatusEmission`` pinning each method when called
directly. Those L1 tests do NOT catch a UI-breakage that prevents the
right-click flow from reaching the method at all (e.g. context-menu
wiring rot, popup menu label drift, the `indexAt(pos).parent()` guard
flipping). This scenario is the layer-3 anchor.

Drives three paths of the four #324 named — paths 1 (Lock / Unlock)
and 2 (Set Action → delete). Path 3 (Remove from List) lives in s54
because the QMessageBox confirm flow is enough surface area to deserve
its own driver. Path 4 (regex apply + LOCK_SENTINEL branch) was
already covered by s32 + ``test_regex_lock_branch_emits_locked_count``.

  scan → close & load →
  seed one decision via main-window right-click (gates the dialog's
    "groups with at least one decision" filter) →
  open Execute Action dialog →
  (a) coord-right-click file row #1 → Lock →
      assert is_locked=1 on that row only;
  (b) coord-right-click the SAME row → Unlock →
      assert is_locked=0 (idempotent path);
  (c) coord-right-click file row #2 → Set Action → delete →
      assert user_decision='delete' on that row only;
  close dialog without Execute.

Sister to s30 (regex right-click in same dialog), s32 (regex-route
lock confirm), s35 (main-window route lock). Same fixture; verification
reads is_locked + user_decision straight from the sqlite manifest.

Coord-based right-click via ``pywinauto.mouse`` rather than UIA
TreeItem traversal: the ExecuteActionDialog's QTreeView doesn't
materialize file rows as UIA TreeItem elements (a PySide6 / Qt-
accessibility quirk specific to that tree — the main window's tree
exposes them fine). The geometry heuristic mirrors s30: aim for the
middle of file row N at ``tree_top + header + group_row + N.5 * row``.
DPI-sensitive — flagged in the issue research; the 0.3 / 0.4 sleeps
around the click match s30's empirically-tuned timing.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pywinauto.mouse

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

# Seed target: pick one row to set the initial decision on so the
# Execute Action dialog has at least one group visible. The choice is
# arbitrary; using the LAST fixture row keeps it visually distinct
# from the rows we're about to right-click in the dialog.
SEED_ROW = "neardup_04_q65.jpg"


def _read_manifest_state() -> dict[str, tuple[bool, str]]:
    """Return ``{basename: (is_locked, user_decision)}`` for every fixture row."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, is_locked, user_decision FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (bool(loc), d or "") for p, loc, d in rows}


def _print_state(label: str, state: dict[str, tuple[bool, str]]) -> None:
    for name in sorted(state):
        locked, dec = state[name]
        glyph = "🔒" if locked else "  "
        print(f"  {label}  {glyph} dec={dec!r:<10} {name}")


def _coord_right_click_file_row(exec_dlg, row_offset: int) -> tuple[int, int]:
    """Coord-right-click the Nth visible file row in the Execute Action dialog.

    ``row_offset`` is 0-indexed within the visible file rows; offset 0
    targets the row that s30 calls the "second file row" (i.e. the
    second file under the group header, which avoids the first Ref-
    tier row's selection oddities). Add 22 px per additional offset
    for subsequent rows.

    The base offset (``tree_rect.top + 105``) is copied verbatim from
    s30 — it's empirically tuned to land in the middle of the targeted
    row across DPI scalings (s30 has been green in qa-batch since PR
    #162). Pre-click with left button to focus before the right-click
    — same pattern as s30 / s35.

    Returns the (cx, cy) used so callers can re-click the same row.
    """
    tree_rect = exec_dlg.descendants(control_type="Tree")[0].rectangle()
    cx = tree_rect.left + (tree_rect.right - tree_rect.left) // 2
    # Match s30's exact base: header + group row + 1.5 file rows = ~105 px
    # down from the tree top. Each additional row_offset adds one file
    # row height (~22 px).
    cy = tree_rect.top + 105 + row_offset * 22
    print(f"  click_coords=({cx},{cy}) tree_rect={tree_rect}")
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    time.sleep(0.3)
    pywinauto.mouse.right_click(coords=(cx, cy))
    time.sleep(0.4)
    return cx, cy


def main() -> int:
    print("scenario: s53_execute_dialog_lock_decision")
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
    initial = _read_manifest_state()
    _print_state("init", initial)
    if not initial:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    if any(loc for loc, _ in initial.values()):
        print("FAIL: fresh manifest has unexpected locked rows")
        return 1
    if any(dec for _, dec in initial.values()):
        print("FAIL: fresh manifest has unexpected pre-set decisions")
        return 1

    # ExecuteActionDialog filters to "groups with at least one decision
    # set" (see _groups_with_decisions in execute_action_dialog.py).
    # With a freshly-scanned manifest nothing has user_decision set yet
    # → the tree would be empty → nothing to right-click. Seed via the
    # main window's right-click flow first; the fixture produces ONE
    # group, so seeding one row pulls all five into the dialog tree.
    print(f"step: seed_one_decision_via_main_tree target={SEED_ROW!r}")
    _uia.left_click_tree_row(win, SEED_ROW)
    _uia.right_click_tree_row(win, SEED_ROW)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])

    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    failures: list[str] = []

    # ── (a) Right-click row 0 → Lock ──────────────────────────────────────
    print("step: dialog_lock_row_0")
    _coord_right_click_file_row(exec_dlg, row_offset=0)
    _uia.select_popup_menu_path(pid, [_uia.CTX_LOCK])
    after_a = _read_manifest_state()
    _print_state("a   ", after_a)
    locked_after_a = {n for n, (loc, _) in after_a.items() if loc}
    if len(locked_after_a) != 1:
        failures.append(
            f"after_a: expected exactly one locked row, got {sorted(locked_after_a)}"
        )

    # ── (b) Right-click the SAME row → Unlock ─────────────────────────────
    print("step: dialog_unlock_row_0")
    _coord_right_click_file_row(exec_dlg, row_offset=0)
    _uia.select_popup_menu_path(pid, [_uia.CTX_UNLOCK])
    after_b = _read_manifest_state()
    _print_state("b   ", after_b)
    locked_after_b = {n for n, (loc, _) in after_b.items() if loc}
    if locked_after_b:
        failures.append(
            f"after_b: expected zero locked rows after unlock, got {sorted(locked_after_b)}"
        )

    # ── (c) Right-click row 1 → Set Action → delete ──────────────────────
    # Decision-via-menu inside the Execute dialog is **deferred** — the
    # dialog's ``_set_decision`` only mutates in-memory ``_groups``;
    # the manifest only sees the change if the user clicks Execute.
    # So we can't verify path 2 by reading the sqlite afterwards.
    # Instead, verify via the status bar's "Decision set" emit added in
    # PR #322 (matches the post-#316 main-window route's confirmation).
    # Same pattern as s30 line 190-192. The status bar belongs to the
    # main window so we read it AFTER closing the dialog.
    print("step: dialog_set_decision_delete_row_1")
    _coord_right_click_file_row(exec_dlg, row_offset=1)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])
    # _set_decision is synchronous; the status emit follows in the
    # same call. Tiny settle to let the QStatusBar paint before we
    # close the dialog and read it from the main window.
    time.sleep(0.2)

    # ── Close (NOT Execute) — keep the scenario non-destructive ──────────
    # Closing without Execute deliberately discards the in-memory
    # 'delete' decision from step (c); the dialog's design treats
    # menu-set decisions as drafts until commit. Verification for
    # step (c) happens via the status bar below, not via the manifest.
    print("step: close_execute_action_dialog")
    close_btn = _uia._find_dialog_button(exec_dlg, "Close")
    close_btn.click_input()

    print("step: assert_status_bar_after_decision")
    _, win = _uia.connect_main()
    if not _invariants.assert_status_bar_matches(win, r"Decision set", within_s=2.0):
        failures.append(
            "step (c): status bar did not echo 'Decision set' after the "
            "dialog right-click → Set Action → delete menu click — "
            "ExecuteActionDialog._set_decision's status_reporter wiring "
            "regressed (#318 / #322)"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s53_execute_dialog_lock_decision DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
