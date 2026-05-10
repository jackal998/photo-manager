"""Scenario 31 — Phase B Simple mode + Phase C regex-sync invariants.

Required source: ``qa/sandbox/near-duplicates`` (5 files, basenames neardup_NN_qXX.jpg).

Phase B introduced a Simple / Regex mode toggle in ActionDialog
(originally "Beginner"; renamed in Phase C). Simple is the default
for new users — they see "Find rows where it [contains | starts with
| ends with | exactly matches] [text]" instead of a regex line edit,
and the dialog synthesises the regex internally so the user never
types a backslash.

Phase C added the regex-sync-across-modes invariant: ``self.regex``
is the single source of truth, so the Simple inputs write through
to the regex line edit on every change. This scenario verifies
both layers:

  scan → close & load → Action menu → Set Action by Field/Regex…
  → assert Simple mode is the active default
  → set op="contains" + text="q9"
  → assert counter shows "1 of 5 match"
  → toggle to Regex mode mid-flow → assert the regex line edit
    holds the synthesised pattern (`q9` for plain text + contains)
  → toggle back to Simple → assert the inputs reverse-parse
    cleanly back to "contains" + "q9"
  → toggle to Regex once more → set action="delete" → Apply →
    verify exactly 1 row was tagged user_decision='delete'
    (neardup_00_q95.jpg — the only one whose basename contains "q9")

Catches drift in: mode-toggle wiring, Simple widget objectNames,
the SIMPLE_OPS → regex builder mapping (the re.escape so special
chars stay literal), the write-through pipeline, the
``_try_parse_simple`` reverse-parse, and the live preview / counter
still reflecting the synthesised pattern.

Distinct from s14 (Regex mode menu route) and s30 (Regex mode
right-click route from Execute dialog) — same fixture, different
entry path through the dialog.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

# Simple mode: op + plain text. With "contains" + "q9" the only
# matching basename is neardup_00_q95.jpg (the q88, q80, q72, q65
# rows don't contain "q9"). Keeps the verification crisp — exactly
# one row should land in user_decision='delete' afterwards.
FIELD = "File Name"
SIMPLE_OP_LABEL = "contains"  # also exact en label of the op combo item
SIMPLE_TEXT = "q9"
# Phase C: the synthesised regex for Simple "contains" + "q9" is
# `re.escape("q9") == "q9"` (no special chars). The regex line edit
# should show this verbatim after the toggle to Regex mid-flow.
EXPECTED_SYNTHESISED_REGEX = "q9"
ACTION_LABEL = "delete"
EXPECTED_TARGET = "neardup_00_q95.jpg"


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


def _select_action_with_retry(action_combo, action_label):
    """Same retry + verify combo-selection used in _drive_action_dialog_form
    (see that helper for the hosted-CI flake rationale)."""
    for _ in range(3):
        try:
            action_combo.set_focus()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            action_combo.select(action_label)
        except Exception:
            pass
        time.sleep(0.4)
        try:
            current = (action_combo.window_text() or "").strip()
        except Exception:
            current = ""
        if current == action_label:
            return


def main() -> int:
    print("scenario: s31_simple_mode_regex")
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
    if EXPECTED_TARGET not in pre:
        print(f"FAIL: expected target {EXPECTED_TARGET!r} not in fixture rows {sorted(pre)}")
        return 1
    print(f"  pre_total={len(pre)}")

    print("step: open_action_dialog_via_menu")
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_BY_REGEX)
    action_hwnd = _uia.wait_for_dialog(pid, _uia.ACTION_DIALOG_TITLE, timeout=5)
    action_dlg = _uia.connect_by_handle(action_hwnd)
    _uia._focus(action_dlg)
    time.sleep(0.3)

    print("step: assert_simple_mode_is_default")
    simple_radio = _uia._find_descendant_by_aid_suffix(
        action_dlg, "RadioButton", ".regexModeSimple"
    )
    regex_radio = _uia._find_descendant_by_aid_suffix(
        action_dlg, "RadioButton", ".regexModeRegex"
    )
    if simple_radio is None or regex_radio is None:
        print("FAIL: mode-toggle radios not found")
        return 1
    try:
        is_simple_default = simple_radio.is_selected()
    except Exception:
        is_simple_default = False
    print(f"  simple_is_default={is_simple_default}")
    if not is_simple_default:
        print("FAIL: Simple mode is not the default")
        return 1

    print("step: select_field")
    field_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexFieldCombo"
    )
    field_combo.select(FIELD)
    time.sleep(0.1)

    print("step: drive_simple_inputs")
    print(f"  op={SIMPLE_OP_LABEL!r} text={SIMPLE_TEXT!r}")
    op_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexSimpleOpCombo"
    )
    if op_combo is None:
        print("FAIL: regexSimpleOpCombo not found")
        return 1
    op_combo.select(SIMPLE_OP_LABEL)
    time.sleep(0.1)
    text_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexSimpleText"
    )
    if text_edit is None:
        print("FAIL: regexSimpleText not found")
        return 1
    text_edit.iface_value.SetValue(SIMPLE_TEXT)
    # Past the 150 ms live-preview debounce.
    time.sleep(0.3)

    print("step: assert_live_preview_counter")
    counter = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Text", ".regexMatchCounter"
    )
    counter_text = counter.window_text() if counter else None
    print(f"  counter_text={counter_text!r}")
    if counter_text is None:
        print("FAIL: live-preview counter not found")
        return 1
    # Simple+contains+'q9' matches exactly 1 row (neardup_00_q95.jpg).
    # Locale-dependent format; just confirm "1" appears.
    if "1" not in counter_text:
        print(f"FAIL: counter {counter_text!r} did not contain expected '1'")
        return 1

    # ── Phase C invariant: regex syncs across modes ─────────────────────
    print("step: toggle_to_regex_and_assert_synthesised_pattern")
    regex_radio.click_input()
    time.sleep(0.3)
    regex_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexLineEdit"
    )
    if regex_edit is None:
        print("FAIL: regexLineEdit not found after toggle to Regex")
        return 1
    regex_value = regex_edit.window_text() if regex_edit else ""
    print(f"  regex_line_edit_value={regex_value!r}")
    if regex_value != EXPECTED_SYNTHESISED_REGEX:
        print(
            f"FAIL: regex line edit shows {regex_value!r}, expected "
            f"{EXPECTED_SYNTHESISED_REGEX!r} (Phase C write-through invariant)"
        )
        return 1

    print("step: toggle_back_to_simple_and_assert_reverse_parse")
    simple_radio.click_input()
    time.sleep(0.3)
    text_edit2 = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexSimpleText"
    )
    # We only assert the text-input value here. The op combo's
    # currentIndex isn't exposed via UIA on a collapsed Qt QComboBox
    # (window_text returns '' and selected_index raises ValueError on
    # the COM pointer) — that branch is covered by unit tests in
    # tests/test_select_dialog.py::TestRegexSyncAcrossModes which have
    # full access to Qt state.
    text_value = text_edit2.window_text() if text_edit2 else ""
    print(f"  text_edit_value={text_value!r}")
    if text_value != SIMPLE_TEXT:
        print(
            f"FAIL: Simple text after Regex→Simple round-trip = "
            f"{text_value!r}; expected {SIMPLE_TEXT!r}"
        )
        return 1

    print("step: apply_action")
    print(f"  action={ACTION_LABEL!r}")
    action_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexActionCombo"
    )
    if action_combo is None:
        print("FAIL: regexActionCombo not found")
        return 1
    _select_action_with_retry(action_combo, ACTION_LABEL)
    apply_btn = _uia._find_dialog_button(action_dlg, _uia.ACTION_DIALOG_BTN_APPLY)
    apply_btn.click_input()
    time.sleep(0.3)
    close_btn = _uia._find_dialog_button(action_dlg, _uia.ACTION_DIALOG_BTN_CLOSE)
    close_btn.click_input()
    time.sleep(0.3)

    print("step: invariant_status_bar")
    _, win = _uia.connect_main()
    if not _invariants.assert_status_bar_matches(win, r"Decision set", within_s=2.0):
        print("WARN: status bar did not echo 'Decision set' (may have cleared on timeout)")

    print("step: verify_decisions_after_apply")
    post = _read_decisions()
    failures: list[str] = []
    for name, decision in sorted(post.items()):
        expected = "delete" if name == EXPECTED_TARGET else pre.get(name, "")
        if decision != expected:
            failures.append(
                f"{name}: expected {expected!r}, got {decision!r}"
            )
        print(f"  row: name={name} pre={pre[name]!r} post={decision!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s31_simple_mode_regex DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
