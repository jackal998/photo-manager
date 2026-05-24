"""Scenario 14 — Action > Set Action by Field (standalone, from menu).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Drives the standalone bulk-decision flow end-to-end:
  scan → close & load → Action menu → Set Action by Field… →
  field=File Name, regex=q[89]\\d, action=delete → Apply → Close →
  verify (a) the 3 matching rows now have user_decision='delete' in the
  manifest, (b) the 2 non-matching rows are unchanged from their pre-state.

Distinct from s13 (which reaches the same handler via Execute Action's
"Select by Field/Regex…" button) — this exercises the Action-menu entry
that ships in main_window's menu bar, the path most users hit first.

Catches drift in: Action menu label / dialog title / dialog widget order
(Field combo first, Action combo second) / set_decision_by_regex match
logic / batch_update_decisions write path / case-insensitive regex flag.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"  # matches all 5 fixture rows

# Regex chosen so the case-insensitive match cleanly partitions the fixture:
# matches q95, q88, q80 (3 rows) and skips q72, q65 (2 rows). If any of those
# numbers ever shifts, this scenario will surface the change.
FIELD = "File Name"
REGEX = r"q[89]\d"
ACTION = "delete"


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
    print("scenario: s14_action_by_regex")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    # #244 — Action menu items that operate on a loaded manifest must
    # start greyed-out and re-enable after Open / Scan. We probe BEFORE
    # the scan so a regression that re-enables the item too early (or
    # never enables it) is caught here, not by user surprise. The full
    # action-menu state is printed so future drift (e.g. a new Action
    # menu item also needing to be gated) is visible in batch logs.
    print("step: assert_action_menu_gated_pre_manifest")
    action_items = _uia.probe_menu_items(win, _uia.MENU_ACTION)
    print(f"  action_menu_items={action_items}")
    gated_state = {title: enabled for title, enabled in action_items}
    if gated_state.get(_uia.ACTION_BY_REGEX, True):
        print(
            f"FAIL: {_uia.ACTION_BY_REGEX!r} is enabled with no "
            f"manifest loaded. Add it to MANIFEST_ACTIONS in "
            f"menu_controller.py — see #244."
        )
        return 1
    if gated_state.get(_uia.ACTION_EXECUTE, True):
        print(
            f"FAIL: {_uia.ACTION_EXECUTE!r} is enabled with no "
            f"manifest loaded — Action menu gating regression."
        )
        return 1

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

    print("step: snapshot_pre_decisions")
    _, win = _uia.connect_main()
    pre = _read_decisions()
    if not pre:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    print(f"  pre_total={len(pre)}")
    print(f"  pre_delete={sum(1 for v in pre.values() if v == 'delete')}")
    print(f"  pre_empty={sum(1 for v in pre.values() if v == '')}")

    # #397 — Apply-button is always enabled. Empty/invalid patterns
    # no longer gate the button; the receiver-side guards in
    # file_operations.set_decision_by_regex surface the failure as a
    # QMessageBox at click-time. This probe inverts the original #354
    # assertion: it pins the new contract that the button stays
    # enabled in both gated cases so a future PR re-introducing
    # setEnabled(False) in _validate_regex trips a visible regression.
    print("step: probe_apply_always_enabled")
    probe_dlg, _ = _uia.open_action_by_regex_dialog(win)
    regex_radio = _uia._find_descendant_by_aid_suffix(
        probe_dlg, "RadioButton", ".regexModeRegex"
    )
    if regex_radio is not None:
        try:
            if not regex_radio.is_selected():
                regex_radio.click_input()
                time.sleep(0.2)
        except Exception:
            pass
    probe_apply = _uia._find_dialog_button(probe_dlg, _uia.ACTION_DIALOG_BTN_APPLY)
    probe_regex = _uia._find_descendant_by_aid_suffix(
        probe_dlg, "Edit", ".regexLineEdit"
    )
    if probe_regex is not None:
        probe_regex.iface_value.SetValue("")
        # Past the 150 ms live-preview debounce so _validate_regex has
        # run before we read the button state.
        time.sleep(0.3)
    _empty_enabled = probe_apply.is_enabled()
    print(f"  probe_status: apply_enabled_empty={_empty_enabled}")
    if _empty_enabled is not True:
        print(
            "FAIL: Apply button is disabled on empty regex — #397 "
            "contract is that it stays enabled and the receiver "
            "surfaces 'No matches'"
        )
        return 1
    if probe_regex is not None:
        probe_regex.iface_value.SetValue("(unclosed")
        time.sleep(0.3)
    _invalid_enabled = probe_apply.is_enabled()
    print(f"  probe_status: apply_enabled_invalid={_invalid_enabled}")
    if _invalid_enabled is not True:
        print(
            "FAIL: Apply button is disabled on invalid regex — #397 "
            "contract is that it stays enabled and the receiver "
            "surfaces 'Invalid Regex'"
        )
        return 1
    _uia.close_action_dialog(probe_dlg)
    _, win = _uia.connect_main()

    # ---------- Probe #361a (Wave 4 A2): Folder-regex preview rows ----------
    # A2 (Wave 4) changed _refresh_preview to render the MATCHED-FIELD
    # string for non-File-Name regexes. Before A2 the preview always
    # showed the basename even when matching against Folder, masking
    # which folder fragment actually drove the match. This probe opens
    # a Folder-regex flow and reads .regexPreviewList — the rows MUST
    # carry a path separator (folder path) and NOT just the basename.
    # No Apply: the probe doesn't change any decisions.
    print("step: probe_folder_regex_preview_shows_paths")
    probe_dlg2, _ = _uia.open_action_by_regex_dialog(win)
    _regex_radio2 = _uia._find_descendant_by_aid_suffix(
        probe_dlg2, "RadioButton", ".regexModeRegex"
    )
    if _regex_radio2 is not None:
        try:
            if not _regex_radio2.is_selected():
                _regex_radio2.click_input()
                time.sleep(0.2)
        except Exception:
            pass
    _field_combo2 = _uia._find_descendant_by_aid_suffix(
        probe_dlg2, "ComboBox", ".regexFieldCombo"
    )
    if _field_combo2 is not None:
        _field_combo2.select("Folder")
        time.sleep(0.2)
    _regex_edit2 = _uia._find_descendant_by_aid_suffix(
        probe_dlg2, "Edit", ".regexLineEdit"
    )
    if _regex_edit2 is not None:
        # "sandbox" is in the folder path of every fixture row but in
        # NO basename — so any preview-row text containing "sandbox"
        # came from the folder field, proving A2's matched-field path.
        _regex_edit2.iface_value.SetValue("sandbox")
        time.sleep(0.4)  # past the 150 ms preview debounce
    _items = _uia.read_preview_items(probe_dlg2)
    print(f"  probe_status: A2-folder-preview-count={len(_items)}")
    if _items:
        for _i, _t in enumerate(_items[:3]):
            print(f"  probe_status: A2-folder-preview-row[{_i}]={_t!r}")
        # A row is folder-shaped if it contains a path separator AND
        # is not just a bare basename. The Wave 4 contract is that the
        # preview shows the matched FIELD value, not the basename.
        _looks_like_path = any(
            ("/" in _t or "\\" in _t) and "sandbox" in _t for _t in _items
        )
        if _looks_like_path:
            print("probe_status: A2-folder-preview-shows-paths PASS")
        else:
            print(
                "probe_status: A2-folder-preview-shows-paths FAIL — no "
                "row contains 'sandbox' with a path separator; preview "
                "may have regressed to bare basenames"
            )
    else:
        print(
            "probe_status: A2-folder-preview-shows-paths SKIP — preview "
            "list empty (regex 'sandbox' should have matched 5 rows)"
        )
    _uia.close_action_dialog(probe_dlg2)
    _, win = _uia.connect_main()

    print("step: apply_regex_via_menu")
    rx = re.compile(REGEX, re.IGNORECASE)
    expected_match = sorted(name for name in pre if rx.search(name))
    expected_unchanged = sorted(name for name in pre if not rx.search(name))
    print(f"  field={FIELD!r} regex={REGEX!r} action={ACTION!r}")
    print(f"  expected_match={expected_match}")
    print(f"  expected_unchanged={expected_unchanged}")
    counter_text = _uia.mark_all_via_regex_standalone(
        win, field=FIELD, regex=REGEX, action_label=ACTION
    )

    print("step: assert_live_preview_counter")
    # The dialog now ships a live-preview pane; the counter must reflect
    # the typed pattern by the time we click Apply. We only verify the
    # text is non-empty and contains digits — exact format is locale-
    # dependent (en: "3 of 5 match", zh_TW: "3 / 5 相符").
    print(f"  counter_text={counter_text!r}")
    if counter_text is None:
        print("FAIL: live-preview counter not found — preview pane missing?")
        return 1
    if not any(ch.isdigit() for ch in counter_text):
        print(f"FAIL: counter text {counter_text!r} has no digits — preview not populated")
        return 1

    print("step: invariant_status_bar")
    _, win = _uia.connect_main()
    if not _invariants.assert_status_bar_matches(win, r"Decision set", within_s=2.0):
        print("WARN: status bar did not echo 'Decision set' (may have cleared on timeout)")

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

    print("scenario: s14_action_by_regex DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
