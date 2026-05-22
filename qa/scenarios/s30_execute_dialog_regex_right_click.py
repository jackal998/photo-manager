"""Scenario 30 — Execute Action dialog: right-click → Set Action by Field/Regex…

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Phase A of the regex-dialog UX upgrade unifies the right-click on file
rows across the main window and the Execute Action dialog. Previously
the Execute Action dialog only exposed regex via a dedicated toolbar
button; non-destructive right-clicks gave you delete / keep / remove
from list but no regex entry. This scenario pins the new behavior:

  scan → close & load → open Execute Action dialog →
  right-click first file row → click "Set Action by Field/Regex…" →
  ActionDialog opens with a populated live-preview pane →
  field=File Name, regex=q[89]\\d, action=delete → assert counter shows
  "3 of 5 match" → Apply → Close inner → Close outer (no execute) →
  verify (a) the 3 matching rows now have user_decision='delete' in
  the manifest, (b) the 2 non-matching rows are unchanged.

Sister to s14 (main-window menu-bar route) and s13 (Execute Action's
toolbar-button route) — same regex partition (q[89]\\d → 3/2), same
verification path, different entry point.

Catches drift in: Execute Action context-menu wiring; new
``execute_dialog.set_action_by_regex_menu`` translation key;
ActionDialog match_fn pass-through from ExecuteActionDialog into the
shared dialog; live-preview counter rendering.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

FIELD = "File Name"
REGEX = r"q[89]\d"
ACTION = "delete"
# Localized menu label for the new right-click entry. Mirrors the
# en.yml value of execute_dialog.set_action_by_regex_menu — drift
# here surfaces as a popup-menu-item-not-found error from
# select_popup_menu_path.
REGEX_MENU_LABEL = "Set Action by Field…"


def _read_decisions() -> dict[str, str]:
    """Return {basename: user_decision} for every fixture row in the manifest."""
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


def main() -> int:
    print("scenario: s30_execute_dialog_regex_right_click")
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
    if not pre:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    print(f"  pre_total={len(pre)}")

    rx = re.compile(REGEX, re.IGNORECASE)
    expected_match = sorted(name for name in pre if rx.search(name))
    expected_unchanged = sorted(name for name in pre if not rx.search(name))
    print(f"  expected_match={expected_match}")
    print(f"  expected_unchanged={expected_unchanged}")
    expected_match_count = len(expected_match)

    # ExecuteActionDialog filters to "groups with at least one decision
    # set" (see _groups_with_decisions). With a freshly-scanned manifest
    # nothing has user_decision set yet → the tree is empty → there's
    # no file row to right-click. Seed one decision via the main window
    # right-click flow first; the tree then has at least one visible
    # group that contains all 5 files (the same group_number).
    print("step: seed_one_decision_via_main_tree")
    seed_target = expected_match[0]
    print(f"  seed_target={seed_target!r}")
    _uia.left_click_tree_row(win, seed_target)
    _uia.right_click_tree_row(win, seed_target)
    _uia.select_popup_menu_path(pid, [_uia.CTX_SET_ACTION, _uia.CTX_DELETE])

    print("step: open_execute_action_dialog")
    exec_dlg, _ = _uia.open_execute_action_dialog(win)

    print("step: right_click_first_file_row")
    # ExecuteActionDialog's QTreeView doesn't materialize file rows as
    # UIA TreeItems (a PySide6/Qt-accessibility quirk specific to that
    # tree — the main window's tree exposes them fine). So we right-
    # click by coordinate. _on_tree_context_menu only shows the popup
    # for FILE rows (children of a group); a click on the group header
    # row makes the handler bail. Layout when expanded:
    #   header (~30 px) → group row (~25 px) → file rows (~22 px each).
    # Aim for the middle of the SECOND file row to leave generous slack
    # for header/row-height variation across DPI scaling.
    import pywinauto.mouse
    tree_rect = exec_dlg.descendants(control_type="Tree")[0].rectangle()
    cx = tree_rect.left + (tree_rect.right - tree_rect.left) // 2
    cy = tree_rect.top + 105  # header + group row + 1.5 file rows
    print(f"  click_coords=({cx},{cy}) tree_rect={tree_rect}")
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    import time
    time.sleep(0.3)
    pywinauto.mouse.right_click(coords=(cx, cy))
    time.sleep(0.4)

    print("step: open_regex_dialog_via_popup")
    # Single-level path: just the regex menu item directly. No "Set
    # Action" submenu in front — the regex entry sits at the bottom of
    # the context menu, below the Set Action submenu and the separator.
    _uia.select_popup_menu_path(pid, [REGEX_MENU_LABEL])

    print("step: drive_action_dialog_form")
    # _drive_action_dialog_form needs the freshly-opened ActionDialog,
    # not the parent. Wait for it by title and connect by handle so the
    # subsequent UIA queries scope to the right window.
    action_hwnd = _uia.wait_for_dialog(
        pid, _uia.ACTION_DIALOG_TITLE, timeout=5
    )
    action_dlg = _uia.connect_by_handle(action_hwnd)
    counter_text = _uia._drive_action_dialog_form(
        action_dlg, field=FIELD, regex=REGEX, action_label=ACTION
    )

    print("step: assert_live_preview_counter")
    print(f"  counter_text={counter_text!r}")
    if counter_text is None:
        print("FAIL: live-preview counter not found — preview pane missing?")
        return 1
    # Format is locale-dependent — verify digits + the expected match
    # count specifically. The counter is "3 of 5 match" (en) or
    # "3 / 5 相符" (zh_TW); both contain "3" since 3 fixtures match.
    if str(expected_match_count) not in counter_text:
        print(
            f"FAIL: counter text {counter_text!r} did not contain expected "
            f"match count {expected_match_count}"
        )
        return 1

    print("step: close_execute_action_dialog")
    # Close (not Execute) — keeps the scenario non-destructive. The
    # decisions were already persisted to the manifest by the regex
    # apply path inside ExecuteActionDialog._set_decision_by_regex.
    # _find_dialog_button picks the bottom-most "Close" so we
    # disambiguate against the title-bar Close on en-US Windows.
    close_btn = _uia._find_dialog_button(exec_dlg, "Close")
    close_btn.click_input()

    print("step: invariant_status_bar")
    _, win = _uia.connect_main()
    # #316 — the Execute Action dialog's regex-apply path must emit the
    # same "Decision set" confirmation as the s14 main-menu route. The
    # bug was that ExecuteActionDialog had no status_reporter plumbed
    # in, so the status bar still showed the stale "Loaded manifest"
    # baseline after the apply. Hard-asserting here per the probe-then-
    # fix pattern: the assertion is paired with the source fix in the
    # same PR so the bug can't regress silently.
    if not _invariants.assert_status_bar_matches(win, r"Decision set", within_s=2.0):
        print("FAIL: status bar did not echo 'Decision set' after regex apply (#316)")
        return 1

    print("step: verify_decisions_after_apply")
    post = _read_decisions()
    if set(post) != set(pre):
        print(f"FAIL: row set changed; pre={sorted(pre)} post={sorted(post)}")
        return 1

    failures: list[str] = []
    for name in expected_match:
        if post[name] != "delete":
            failures.append(f"{name}: expected 'delete', got {post[name]!r}")
    for name in expected_unchanged:
        if post[name] != pre[name]:
            failures.append(
                f"{name}: expected unchanged ({pre[name]!r}), got {post[name]!r}"
            )

    matched_rows = sum(1 for name in expected_match if post[name] == "delete")
    unchanged_rows = sum(
        1 for name in expected_unchanged if post[name] == pre[name]
    )
    print(f"  matched_rows={matched_rows} expected={len(expected_match)}")
    print(f"  unchanged_rows={unchanged_rows} expected={len(expected_unchanged)}")
    for name in sorted(post):
        print(f"  row: name={name} pre={pre[name]!r} post={post[name]!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s30_execute_dialog_regex_right_click DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
