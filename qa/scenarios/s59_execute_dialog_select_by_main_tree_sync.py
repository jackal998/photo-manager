"""Scenario 59 — Execute Action dialog Select-by → main tree sync (#444).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Sister to s30 (covers regex right-click round-trip through the SQLite
manifest). s30 closes the Execute Action dialog without verifying the
**main window tree's rendered cells** — that's the gap this scenario
closes for #444. The bug being pinned:

  Inside the Execute Action dialog, the user clicks Select by Field/
  Regex…, applies a regex, then clicks Close (not Execute). The records
  are persisted to SQLite and ``vm.groups`` is updated in place (the
  dialog aliases the same list), but the main tree's QStandardItemModel
  is never rebuilt — its rendered ``Action`` cells still show the
  pre-change values.

  Pre-#444 fix: ``file_operations.execute_action`` only called
  ``refresh_tree`` when ``accepted=True`` OR ``removed_from_list_paths``
  was non-empty. The "Close after Select-by decision changes" path
  fell through silently.

  Post-#444 fix: ``ExecuteActionDialog`` sets ``_decisions_changed=True``
  on any in-dialog batch decision/lock mutation; the handler reads the
  flag on reject and calls ``refresh_tree`` to re-sync the main tree's
  rendered cells with vm.groups.

Flow:

  scan → close & load →
  seed one decision via main-window right-click on a row that is NOT
    expected to match the upcoming regex (so we can isolate the
    Select-by-driven changes from the seed) →
  open Execute Action dialog →
  right-click first file row → Set Action by Field… → apply regex
    targeting 3 of 5 rows with action=delete → Apply → Close inner →
  Close outer (no Execute) →
  verify (a) the manifest has the 3 expected new 'delete' rows
  (proves the Select-by persistence end-to-end), AND
  (b) the main window's tree shows 'delete' for those 3 rows
  (proves refresh_tree fired — the gap before #444).

The (b) assertion is the new coverage. (a) overlaps with s30 but is
included for symmetry — if (a) regresses but (b) doesn't, the bug is
in persistence, not in the sync path.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path

import pywinauto.mouse

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

FIELD = "File Name"
REGEX = r"q[89]\d"
ACTION = "delete"
REGEX_MENU_LABEL = "Set Action by Field…"

# Pick a seed row that does NOT match the regex. The fixture
# basenames embed the quality marker as ``qNN`` — q[89]\d hits q80-q99.
# neardup_04_q65.jpg is below the regex range so seeding 'delete' on it
# stays orthogonal to what Select-by does inside the dialog.
SEED_ROW = "neardup_04_q65.jpg"


def _read_decisions() -> dict[str, str]:
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: (d or "") for p, d in rows}


def _read_main_tree_actions(win) -> dict[str, str]:
    """Map basename → Action-column text from the main window tree.

    Re-uses ``read_result_rows``: each row's cells are screen-clustered
    by y-coord and the Action column appears alongside the File Name
    cell when the row has ``user_decision`` set. Rows whose action is
    "" (the keep / undecided default) don't surface an action cell, so
    a missing entry here means "shown as empty action" in the tree —
    which is what an un-refreshed main tree would still show pre-#444.

    Caller is expected to grep for the localised action label
    ("delete") inside each row's cells tuple. The basename is found
    by matching the cell text against the fixture's basename glob.
    """
    rows = _uia.read_result_rows(win)
    result: dict[str, str] = {}
    for row in rows:
        basename = None
        action = ""
        for cell in row.cells:
            if FIXTURE_NAME_GLOB in cell and cell.endswith(".jpg"):
                basename = cell
            elif cell.lower() in ("delete", "keep"):
                action = cell.lower()
        if basename is not None:
            result[basename] = action
    return result


def main() -> int:
    print("scenario: s59_execute_dialog_select_by_main_tree_sync")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: snapshot_pre_decisions")
    pre = _read_decisions()
    if not pre:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1

    rx = re.compile(REGEX, re.IGNORECASE)
    expected_match = sorted(name for name in pre if rx.search(name))
    expected_unchanged_by_regex = sorted(
        name for name in pre if not rx.search(name)
    )
    print(f"  expected_match_by_regex={expected_match}")
    print(f"  expected_unchanged_by_regex={expected_unchanged_by_regex}")
    if SEED_ROW not in expected_unchanged_by_regex:
        print(
            f"FAIL: SEED_ROW={SEED_ROW!r} unexpectedly matches the regex — "
            "isolation invariant for this scenario broken"
        )
        return 1

    # Seed exactly one decision via main-tree right-click so the
    # Execute Action dialog has a group to show. Pick the non-matching
    # row so we can attribute every dialog-driven change to Select-by.
    print(f"step: seed_one_decision_via_main_tree target={SEED_ROW!r}")
    _uia.left_click_tree_row(win, SEED_ROW)
    _uia.right_click_tree_row(win, SEED_ROW)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])

    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    print("step: right_click_first_file_row")
    # Coord-right-click — same shape as s30 (the dialog's QTreeView
    # doesn't surface file rows as UIA TreeItems).
    tree_rect = exec_dlg.descendants(control_type="Tree")[0].rectangle()
    cx = tree_rect.left + (tree_rect.right - tree_rect.left) // 2
    cy = tree_rect.top + 105
    print(f"  click_coords=({cx},{cy}) tree_rect={tree_rect}")
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    time.sleep(0.3)
    pywinauto.mouse.right_click(coords=(cx, cy))
    time.sleep(0.4)

    print("step: open_select_by_dialog_via_popup")
    _uia.select_popup_menu_path(pid, [REGEX_MENU_LABEL])

    print("step: drive_action_dialog_form")
    action_hwnd = _uia.wait_for_dialog(
        pid, _uia.ACTION_DIALOG_TITLE, timeout=5
    )
    action_dlg = _uia.connect_by_handle(action_hwnd)
    counter_text = _uia._drive_action_dialog_form(
        action_dlg, field=FIELD, regex=REGEX, action_label=ACTION
    )
    print(f"  counter_text={counter_text!r}")

    print("step: close_execute_action_dialog")
    # Click Close (NOT Execute) — this is the exact path #444 fixed.
    # Pre-fix: the in-dialog regex apply persisted to SQLite and
    # mutated vm.groups, but the main tree did NOT refresh. Post-fix:
    # _decisions_changed flips True inside the dialog → handler reads
    # the flag on reject → refresh_tree fires.
    close_btn = _uia._find_dialog_button(exec_dlg, "Close")
    close_btn.click_input()
    time.sleep(0.4)
    _, win = _uia.connect_main()

    failures: list[str] = []

    # (a) Manifest reflects the Select-by changes (overlaps s30 but
    # asserted here so a failure narrows the bug surface — DB layer
    # vs UI sync layer).
    print("step: verify_manifest_after_close")
    post = _read_decisions()
    for name in expected_match:
        if post[name] != "delete":
            failures.append(
                f"manifest {name}: expected 'delete' after Select-by, "
                f"got {post[name]!r}"
            )
    # SEED_ROW must still be 'delete' from the main-tree seed step.
    if post.get(SEED_ROW) != "delete":
        failures.append(
            f"manifest {SEED_ROW}: expected 'delete' (seed), "
            f"got {post.get(SEED_ROW)!r}"
        )

    # (b) Main tree rendering reflects the changes. Pre-#444 fix this
    # was the failing assertion: refresh_tree was never called on the
    # reject-after-Select-by path, so the tree still showed the
    # pre-change Action cells.
    print("step: verify_main_tree_after_close")
    tree_actions = _read_main_tree_actions(win)
    print(f"  tree_actions={tree_actions}")
    for name in expected_match:
        if tree_actions.get(name) != "delete":
            failures.append(
                f"main tree {name}: expected rendered 'delete' after "
                f"Select-by + Close, got {tree_actions.get(name)!r} — "
                f"refresh_tree did not fire on reject (#444 regression)"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s59_execute_dialog_select_by_main_tree_sync DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
