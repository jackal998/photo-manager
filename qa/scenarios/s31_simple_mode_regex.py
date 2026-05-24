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

  scan → close & load → Action menu → Set Action by Field…
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

    # #354 — Apply-button gating probe (Simple-mode variant).
    # Layer-1 tests in tests/test_select_dialog.py::TestApplyGate cover
    # the no-emit rule on _emit_set_action when the pattern is empty or
    # invalid, but they don't observe the live button's enabled state.
    # This probe reads dlg._btn_set_action.isEnabled() through UIA for
    # both gate paths so a future PR that rewires the button (e.g. a
    # QToolButton swap, or dropping the setEnabled(False) calls in
    # _validate_regex) trips a visible regression here. Probe state is
    # restored before the existing happy-path flow continues.
    print("step: probe_apply_gate")
    probe_apply = _uia._find_dialog_button(
        action_dlg, _uia.ACTION_DIALOG_BTN_APPLY
    )
    # Empty-text case — Simple text is empty by default on dialog open.
    # The 150 ms live-preview debounce has already elapsed by the time
    # we get here (the assert_simple_mode_is_default block above takes
    # well over 150 ms), so the gate has settled.
    print(f"  probe_status: apply_enabled_empty={probe_apply.is_enabled()}")
    # Invalid-pattern case — Simple mode synthesises via re.escape so
    # no Simple input is ever syntactically invalid; the gate path that
    # disables Apply on re.error lives behind the Regex-mode line edit
    # (select_dialog.py line 1266). Toggle into Regex mode to exercise
    # it, then restore Simple state so the existing happy path runs
    # unchanged.
    regex_radio.click_input()
    time.sleep(0.3)
    probe_regex_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexLineEdit"
    )
    if probe_regex_edit is not None:
        probe_regex_edit.iface_value.SetValue("(unclosed")
        time.sleep(0.3)
    print(f"  probe_status: apply_enabled_invalid={probe_apply.is_enabled()}")
    # Clear the line edit before toggling back so Simple's reverse-parse
    # doesn't see the malformed "(unclosed" pattern.
    if probe_regex_edit is not None:
        probe_regex_edit.iface_value.SetValue("")
        time.sleep(0.2)
    simple_radio.click_input()
    time.sleep(0.3)

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
    # D3 from #350 (Wave 10): if ACTION_LABEL is "delete", the new
    # DeleteRegexConfirmDialog appears between Apply and the post-emit
    # state. Auto-dismiss with Confirm so the rest of this scenario
    # (close + status-bar invariant) sees the post-apply state. No-op
    # for non-delete actions (short timeout returns False).
    _uia.drive_delete_regex_confirm(action_dlg.process_id(), confirm=True)
    _uia.close_action_dialog(action_dlg)

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


    # ---------- Probe A7: recent-pattern pick flips to Simple mode ----------
    # Wave-7 (A7) added _apply_recent_pattern logic so that picking a
    # Simple-representable pattern from the Recent menu flips the mode to
    # Simple. This probe verifies the UIA-observable side: after clicking
    # the recent "q9" entry (recorded during the Apply step above), the
    # Simple radio must be checked.
    # Promote when regression detected: swap print -> failures.append.
    print("step: probe_a7_recent_picks_simple")
    _, win = _uia.connect_main()
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_BY_REGEX)
    probe_dlg_hwnd = _uia.wait_for_dialog(pid, _uia.ACTION_DIALOG_TITLE, timeout=5)
    probe_dlg = _uia.connect_by_handle(probe_dlg_hwnd)
    _uia._focus(probe_dlg)
    time.sleep(0.4)
    # Apply above recorded ("File Name", "q9") in Recent. Click the Recent
    # button to open the popup, then click the "q9" menu entry.
    probe_recent_btn = _uia._find_descendant_by_aid_suffix(
        probe_dlg, "Button", ".regexRecentButton"
    )
    if probe_recent_btn is None:
        print("probe_status: A7-recent-picks-simple FAIL — regexRecentButton not found")
    else:
        probe_recent_btn.click_input()
        time.sleep(0.4)
        probe_popup_hwnd = _uia.find_popup(pid)
        if probe_popup_hwnd is None:
            print("probe_status: A7-recent-picks-simple FAIL — Recent menu popup did not appear")
        else:
            probe_popup = _uia.connect_by_handle(probe_popup_hwnd)
            try:
                probe_popup.child_window(
                    title=SIMPLE_TEXT, control_type="MenuItem"
                ).click_input()
                time.sleep(0.4)
                probe_simple_radio = _uia._find_descendant_by_aid_suffix(
                    probe_dlg, "RadioButton", ".regexModeSimple"
                )
                if probe_simple_radio is not None and probe_simple_radio.is_selected():
                    print("probe_status: A7-recent-picks-simple PASS")
                else:
                    _selected = probe_simple_radio.is_selected() if probe_simple_radio else None
                    print(
                        f"probe_status: A7-recent-picks-simple FAIL — "
                        f"simple_radio.is_selected()={_selected!r}"
                    )
            except Exception as _exc:
                print(f"probe_status: A7-recent-picks-simple FAIL — {_exc!r}")
    # ---------- end probe A7 ----------

    # Close the A7 probe dialog before opening the B2 probe dialog.
    # B2 opens its own fresh dialog (#386 fix) — reusing probe_dlg here
    # left it in a state where the UIA tree was stale after the Recent
    # menu's popup + click_input, and subsequent lookups for
    # .regexModeRegex / .regexLineEdit returned None.
    try:
        _uia.close_action_dialog(probe_dlg)
    except Exception:
        pass

    # ---------- Probe B2: Switch-to-Regex button appears and works ----------
    # Wave-7 (B2+B4) added _switch_to_regex_btn inside simple_outer —
    # shown alongside _simple_complex_notice when the current regex is not
    # Simple-representable. Clicking it flips to Regex losslessly.
    #
    # #386 fix: open a fresh dialog. The previous version reused
    # probe_dlg from the A7 probe above, but A7's Recent-menu pick
    # left the wrapper's UIA tree stale (a Recent-menu pick flips to
    # Simple mode and may close + reopen internal layout widgets),
    # so .regexModeRegex / .regexLineEdit lookups returned None.
    print("step: probe_b2_switch_to_regex_button")
    _, win = _uia.connect_main()
    probe_b2_dlg, _ = _uia.open_action_by_regex_dialog(win)
    time.sleep(0.5)
    # The dialog opens in whichever mode the prior probe persisted —
    # typically Simple after the main-flow Apply. In Simple mode the
    # regexLineEdit widget is HIDDEN, and UIA doesn't surface hidden
    # widgets via descendants(). So we walk the radios first, click
    # Regex (which surfaces regexLineEdit), then walk Edit descendants.
    # (#386 root cause: original probe looked up both radio + edit
    # before the mode switch and the edit lookup returned None.)
    probe_regex_radio = _uia._find_descendant_by_aid_suffix(
        probe_b2_dlg, "RadioButton", ".regexModeRegex"
    )
    probe_simple_radio2 = _uia._find_descendant_by_aid_suffix(
        probe_b2_dlg, "RadioButton", ".regexModeSimple"
    )
    _COMPLEX = r"\d{3}"
    probe_regex_edit2 = None
    if probe_regex_radio is None or probe_simple_radio2 is None:
        print("probe_status: B2-switch-to-regex FAIL — mode-toggle radios not found")
    else:
        probe_regex_radio.click_input()
        time.sleep(0.4)
        # Now Regex mode is active — regexLineEdit is in the UIA tree.
        probe_regex_edit2 = _uia._find_descendant_by_aid_suffix(
            probe_b2_dlg, "Edit", ".regexLineEdit"
        )
    if probe_regex_edit2 is None:
        # Bail out of the B2 probe — without the line edit we can't
        # set a non-Simple pattern. Other Wave 11 probes still need
        # to run, so close the dialog and fall through (NOT return).
        if probe_regex_radio is not None:
            print(
                "probe_status: B2-switch-to-regex FAIL — regexLineEdit "
                "still missing after switching to Regex mode (hidden?)"
            )
    else:
        probe_regex_edit2.iface_value.SetValue(_COMPLEX)
        time.sleep(0.2)
        probe_simple_radio2.click_input()
        time.sleep(0.4)
        probe_switch_btn = _uia._find_descendant_by_aid_suffix(
            probe_b2_dlg, "Button", ".regexSwitchToRegexBtn"
        )
        if probe_switch_btn is None:
            print("probe_status: B2-switch-to-regex FAIL — regexSwitchToRegexBtn not in UIA tree")
        elif probe_switch_btn.is_visible():
            probe_switch_btn.click_input()
            time.sleep(0.3)
            # After Switch-to-Regex click, re-resolve the regex radio
            # — the wrapper for the still-Simple-mode-snapshotted radio
            # would read its old is_selected() state. Walk fresh.
            probe_regex_radio2 = None
            for _r in probe_b2_dlg.descendants(control_type="RadioButton"):
                try:
                    _aid = _r.element_info.automation_id or ""
                except Exception:
                    _aid = ""
                if _aid.endswith(".regexModeRegex"):
                    probe_regex_radio2 = _r
                    break
            _regex_checked = (
                probe_regex_radio2 is not None and probe_regex_radio2.is_selected()
            )
            _pattern_ok = probe_regex_edit2.window_text() == _COMPLEX
            if _regex_checked and _pattern_ok:
                print("probe_status: B2-switch-to-regex PASS")
            else:
                print(
                    f"probe_status: B2-switch-to-regex FAIL — "
                    f"regex_checked={_regex_checked} pattern_preserved={_pattern_ok}"
                )
        else:
            print(
                "probe_status: B2-switch-to-regex FAIL — "
                "regexSwitchToRegexBtn present but not visible after Simple-toggle "
                "with complex pattern"
            )
    try:
        _uia.close_action_dialog(probe_b2_dlg)
    except Exception:
        pass
    # ---------- end probe B2 ----------

    # ════════════════════════════════════════════════════════════════════
    # Wave 11 probes — layer-3 coverage push (issues #359 #366 #374 #377 #379)
    # Each probe opens its own ActionDialog so prior probe state cannot
    # bleed into the next. Probes don't change manifest decisions —
    # the load-bearing decision-write was already verified in the main
    # flow above. Failures here surface UI-wiring regressions that
    # layer-1 (which mocks Qt) cannot observe.
    # ════════════════════════════════════════════════════════════════════

    # ---------- Probe #359 (Wave 3 A1): regex survives field change ----------
    # SKIP-soft. A1's preservation logic in _on_field_changed compares
    # current_text against re.escape(prev_field_row_value) and only
    # preserves when they differ — driving this through pywinauto turned
    # out fragile (Simple-panel reverse-parse + Mode-toggle order can
    # re-stamp a trailing \. on the user's regex depending on what
    # row_values[prev_field] is at probe-time). The contract is fully
    # covered at layer 1 in tests/test_select_dialog.py::
    # TestFieldChangeStateCorrectness which has direct access to
    # _previous_field and _row_values. Tracking on #359.
    print("step: probe_a1_regex_survives_field_change")
    print(
        "probe_status: A1-regex-survives-field-change SKIP — "
        "preservation contract reproducible only with direct access to "
        "_previous_field / _row_values; layer-1 "
        "TestFieldChangeStateCorrectness is the contract. Tracking on #359."
    )

    # ---------- Probe #366 (Wave 7 E3+E8): mode/field round-trip ----------
    # E3+E8 (Wave 7): mode, field, and simple_op preferences persist
    # per context across close-and-reopen. We set a non-default Field
    # ("Folder") in Simple mode, close, then read the persisted value
    # from qa/settings.json directly — collapsed Qt QComboBox does NOT
    # expose its current item via UIA Name property, so reading from
    # the persistence layer is the load-bearing assertion. The
    # persisted key is ui.action_dialog.main.field (context_id="main"
    # is the menu-route context the s31 dialog uses). C1 + A8 + A6
    # require setups s31 can't drive cleanly; SKIP-soft and document
    # on the issue.
    print("step: probe_e3e8_field_persists_across_reopen")
    _, win = _uia.connect_main()
    _dlg366a, _ = _uia.open_action_by_regex_dialog(win)
    _field_combo366a = _uia._find_descendant_by_aid_suffix(
        _dlg366a, "ComboBox", ".regexFieldCombo"
    )
    if _field_combo366a is not None:
        try:
            _field_combo366a.select("Folder")
            time.sleep(0.3)
        except Exception as _e:
            print(f"  probe_status: E3E8-set-field-attempt FAIL — {_e!r}")
    try:
        _uia.close_action_dialog(_dlg366a)
    except Exception:
        pass
    # Read the persisted field from settings.json — the contract is
    # "persists across close-and-reopen"; the persistence layer IS the
    # contract. Reopening the dialog is downstream of this (and the
    # combo's current-item is unreliable through UIA — see above).
    import json as _json
    _settings_path = REPO / "qa" / "settings.json"
    _persisted_field = None
    try:
        with open(_settings_path, encoding="utf-8") as _sf:
            _persisted_field = (
                _json.load(_sf)
                .get("ui", {})
                .get("action_dialog", {})
                .get("main", {})
                .get("field")
            )
    except Exception as _e:
        print(f"  probe_status: E3E8-settings-read FAIL — {_e!r}")
    print(f"  probe_status: E3E8-persisted-field={_persisted_field!r}")
    if _persisted_field == "Folder":
        print("probe_status: E3E8-field-persists PASS")
    else:
        print(
            f"probe_status: E3E8-field-persists FAIL — "
            f"persisted {_persisted_field!r}, expected 'Folder' — "
            f"ui.action_dialog.main.field persistence regressed"
        )
    # Restore default field so the next probe starts clean. Reopen the
    # dialog ONCE to flip the persisted field back via the normal flow.
    _, win = _uia.connect_main()
    _dlg366b, _ = _uia.open_action_by_regex_dialog(win)
    _field_combo366b = _uia._find_descendant_by_aid_suffix(
        _dlg366b, "ComboBox", ".regexFieldCombo"
    )
    if _field_combo366b is not None:
        try:
            _field_combo366b.select(FIELD)
            time.sleep(0.2)
        except Exception:
            pass
    try:
        _uia.close_action_dialog(_dlg366b)
    except Exception:
        pass
    print(
        "probe_status: C1-disabled-when-no-match-fn SKIP — "
        "s31's post-scan session always has match_fn; no-manifest "
        "open path needs a dedicated scenario. Tracking on #366."
    )
    print(
        "probe_status: A8-context-isolation SKIP — cross-context "
        "verification needs both menu (context_id='main') and Execute "
        "(context_id='execute') open with divergent settings; the "
        "settings-divergence setup needs more scaffolding than s31 "
        "warrants. Tracking on #366."
    )
    print(
        "probe_status: A6-cross-field-filtering SKIP — needs "
        "pre-existing Recent menu entries for one field that should "
        "NOT show under another; Recent state is wiped between batch "
        "runs. Tracking on #366."
    )

    # ---------- Probe #377 B12: action label text ----------
    # B12 (Wave 9b-trim): label above the action combo rewords from
    # "Set Action:" to "Action for each match:" to make per-row scope
    # explicit. No objectName on the QLabel (line 1038 of
    # select_dialog.py) — walk Text descendants and match by text.
    print("step: probe_b12_action_label_text")
    _, win = _uia.connect_main()
    _dlg377, _ = _uia.open_action_by_regex_dialog(win)
    _found_label = False
    try:
        for _t in _dlg377.descendants(control_type="Text"):
            try:
                _txt = (_t.window_text() or "").strip()
            except Exception:
                _txt = ""
            if _txt == "Action for each match:":
                _found_label = True
                break
    except Exception as _e:
        print(f"  probe_status: B12-action-label-walk FAIL — {_e!r}")
    print(f"  probe_status: B12-action-label-present={_found_label}")
    if _found_label:
        print("probe_status: B12-action-label-text PASS")
    else:
        # Locale fallback — match the zh_TW translation too in case
        # the runner is non-English.
        _found_zh = False
        try:
            for _t in _dlg377.descendants(control_type="Text"):
                try:
                    _txt = (_t.window_text() or "").strip()
                except Exception:
                    _txt = ""
                if "每組相符" in _txt or "對每筆相符" in _txt:
                    _found_zh = True
                    break
        except Exception:
            pass
        if _found_zh:
            print("probe_status: B12-action-label-text PASS (zh)")
        else:
            print(
                "probe_status: B12-action-label-text FAIL — neither "
                "en 'Action for each match:' nor zh equivalents found "
                "in dialog Text descendants"
            )

    # D4 probe removed in #395 — "Test against" playground was dropped
    # because the live-preview pane already shows real matches against
    # the loaded manifest. Close the B12 dialog before the next probe
    # opens its own.
    try:
        _uia.close_action_dialog(_dlg377)
    except Exception:
        pass

    # ---------- Probe #377 B9 + #379 D3 cancel ----------
    # B9 (Wave 9b-trim): post-Apply, counter briefly shows
    # "Applied to N rows" before reverting on next debounce.
    # D3 (Wave 10): delete action triggers DeleteRegexConfirmDialog;
    # clicking Cancel must NOT emit setActionRequested (no manifest
    # write). The two probes share a dialog session because both
    # involve Apply on the delete action.
    print("step: probe_b9_d3_apply_flash_and_cancel")
    _, win = _uia.connect_main()
    _dlg_b9, _ = _uia.open_action_by_regex_dialog(win)
    _regex_radio_b9 = _uia._find_descendant_by_aid_suffix(
        _dlg_b9, "RadioButton", ".regexModeRegex"
    )
    if _regex_radio_b9 is not None:
        _regex_radio_b9.click_input()
        time.sleep(0.3)
    _field_combo_b9 = _uia._find_descendant_by_aid_suffix(
        _dlg_b9, "ComboBox", ".regexFieldCombo"
    )
    if _field_combo_b9 is not None:
        try:
            _field_combo_b9.select(FIELD)
            time.sleep(0.2)
        except Exception:
            pass
    _regex_edit_b9 = _uia._find_descendant_by_aid_suffix(
        _dlg_b9, "Edit", ".regexLineEdit"
    )
    # Pick a pattern with at least one match so the flash text has a
    # non-zero count — the main flow already set neardup_00_q95 to
    # delete, so we pick a different fixture row to keep the
    # decision-state isolated. q88 is still in 'pre' state.
    if _regex_edit_b9 is not None:
        _regex_edit_b9.iface_value.SetValue("q88")
        time.sleep(0.3)
    _action_combo_b9 = _uia._find_descendant_by_aid_suffix(
        _dlg_b9, "ComboBox", ".regexActionCombo"
    )
    if _action_combo_b9 is not None:
        _select_action_with_retry(_action_combo_b9, "keep")
    # First Apply: action=keep (no D3 confirm modal — only delete
    # triggers it). Verify the B9 "Applied to" flash text surfaces.
    _apply_btn_b9 = _uia._find_dialog_button(_dlg_b9, _uia.ACTION_DIALOG_BTN_APPLY)
    _apply_btn_b9.click_input()
    time.sleep(0.3)
    # No confirm modal for 'keep' — but call drive in case something
    # changes; short timeout returns False harmlessly.
    _uia.drive_delete_regex_confirm(_dlg_b9.process_id(), confirm=True)
    _counter_b9 = _uia._find_descendant_by_aid_suffix(
        _dlg_b9, "Text", ".regexMatchCounter"
    )
    _counter_text_b9 = (_counter_b9.window_text() if _counter_b9 else "") or ""
    print(f"  probe_status: B9-counter-after-apply={_counter_text_b9!r}")
    if "Applied to" in _counter_text_b9 or "已套用" in _counter_text_b9:
        print("probe_status: B9-post-apply-flash PASS")
    else:
        print(
            f"probe_status: B9-post-apply-flash FAIL — counter "
            f"{_counter_text_b9!r} missing 'Applied to'/'已套用' marker; "
            f"match_counter_applied template may have regressed"
        )

    # Now exercise D3 cancel: switch action to delete, Apply, hit
    # Cancel on the confirm modal, assert no new decisions written.
    print("step: probe_d3_cancel_does_not_emit")
    _pre_d3 = _read_decisions()
    if _action_combo_b9 is not None:
        _select_action_with_retry(_action_combo_b9, "delete")
    _apply_btn_b9.click_input()
    time.sleep(0.3)
    _cancelled = _uia.drive_delete_regex_confirm(
        _dlg_b9.process_id(), confirm=False, timeout=3.0
    )
    print(f"  probe_status: D3-cancel-modal-dismissed={_cancelled}")
    time.sleep(0.5)
    _post_d3 = _read_decisions()
    # Decisions table should be byte-for-byte identical — no row
    # tagged 'delete' as a result of the cancelled Apply.
    if _cancelled and _post_d3 == _pre_d3:
        print("probe_status: D3-cancel-does-not-emit PASS")
    else:
        _diff = {
            name: (_pre_d3.get(name), _post_d3.get(name))
            for name in set(_pre_d3) | set(_post_d3)
            if _pre_d3.get(name) != _post_d3.get(name)
        }
        print(
            f"probe_status: D3-cancel-does-not-emit FAIL — "
            f"cancelled={_cancelled}, decision_diff={_diff!r}; "
            f"setActionRequested should NOT fire when D3 modal is "
            f"cancelled (#350 D3 contract)"
        )
    try:
        _uia.close_action_dialog(_dlg_b9)
    except Exception:
        pass

    # ---------- Probe #374 D9 Ctrl+Enter + D10 Alt mnemonics ----------
    # D9 (Wave 9a): Ctrl+Return triggers Apply same as the button.
    # D10 (Wave 9a): Alt+A=Apply, Alt+C=Close, Alt+R=Recent, Alt+S=
    # Switch to Regex, Alt+W=Reset window size. Verify Alt+A fires
    # Apply (counter changes to "Applied to" flash) and Alt+R opens
    # the Recent menu popup. B11 (tooltip hover) is SKIP-soft because
    # Qt tooltip surfacing via UIA on Windows 10 is unreliable —
    # layer-1 toolTip() getter pins the contract.
    print("step: probe_d9_d10_keyboard_shortcuts")
    _, win = _uia.connect_main()
    _dlg_kb, _ = _uia.open_action_by_regex_dialog(win)
    _regex_radio_kb = _uia._find_descendant_by_aid_suffix(
        _dlg_kb, "RadioButton", ".regexModeRegex"
    )
    if _regex_radio_kb is not None:
        _regex_radio_kb.click_input()
        time.sleep(0.3)
    _field_combo_kb = _uia._find_descendant_by_aid_suffix(
        _dlg_kb, "ComboBox", ".regexFieldCombo"
    )
    if _field_combo_kb is not None:
        try:
            _field_combo_kb.select(FIELD)
            time.sleep(0.2)
        except Exception:
            pass
    _regex_edit_kb = _uia._find_descendant_by_aid_suffix(
        _dlg_kb, "Edit", ".regexLineEdit"
    )
    if _regex_edit_kb is not None:
        _regex_edit_kb.iface_value.SetValue("q65")
        time.sleep(0.3)
    _action_combo_kb = _uia._find_descendant_by_aid_suffix(
        _dlg_kb, "ComboBox", ".regexActionCombo"
    )
    if _action_combo_kb is not None:
        _select_action_with_retry(_action_combo_kb, "keep")
    # D9 Ctrl+Return → Apply should fire (counter flashes "Applied to").
    try:
        _uia._focus(_dlg_kb)
        # Re-focus the regex edit so Ctrl+Return fires the dialog's
        # shortcut, not the action combo's default-button equivalent.
        if _regex_edit_kb is not None:
            _regex_edit_kb.set_focus()
            time.sleep(0.1)
        _dlg_kb.type_keys("^{ENTER}")
        time.sleep(0.5)
        _uia.drive_delete_regex_confirm(_dlg_kb.process_id(), confirm=True)
        _counter_d9 = _uia._find_descendant_by_aid_suffix(
            _dlg_kb, "Text", ".regexMatchCounter"
        )
        _counter_text_d9 = (
            _counter_d9.window_text() if _counter_d9 else ""
        ) or ""
        print(f"  probe_status: D9-counter-after-ctrl-enter={_counter_text_d9!r}")
        if "Applied to" in _counter_text_d9 or "已套用" in _counter_text_d9:
            print("probe_status: D9-ctrl-enter-triggers-apply PASS")
        else:
            print(
                f"probe_status: D9-ctrl-enter-triggers-apply FAIL — "
                f"counter {_counter_text_d9!r} did not flash 'Applied to'; "
                f"QShortcut(Ctrl+Return) wiring may have regressed"
            )
    except Exception as _exc:
        print(f"probe_status: D9-ctrl-enter-triggers-apply FAIL — {_exc!r}")

    # D10 Alt+R should open the Recent menu popup. Verify by polling
    # for a new popup hwnd. (Alt+A would fire Apply again; we already
    # tested that via D9. Alt+R is the most distinctive — it opens a
    # popup rather than firing a button.)
    print("step: probe_d10_alt_r_opens_recent")
    try:
        _uia._focus(_dlg_kb)
        time.sleep(0.2)
        _dlg_kb.type_keys("%r")
        time.sleep(0.4)
        _popup_hwnd = _uia.find_popup(_dlg_kb.process_id())
        if _popup_hwnd is not None:
            print("probe_status: D10-alt-r-opens-recent PASS")
            # Dismiss the popup so it doesn't interfere with the Close.
            try:
                _uia._user32.keybd_event(0x1B, 0, 0, 0)
                _uia._user32.keybd_event(0x1B, 0, 2, 0)
                time.sleep(0.2)
            except Exception:
                pass
        else:
            print(
                "probe_status: D10-alt-r-opens-recent FAIL — no popup "
                "appeared after Alt+R; mnemonic wiring on Recent button "
                "may have regressed (label should be '&Recent')"
            )
    except Exception as _exc:
        print(f"probe_status: D10-alt-r-opens-recent FAIL — {_exc!r}")
    print(
        "probe_status: B11-tooltip-hover SKIP — Qt tooltip text "
        "surfacing via UIA on Windows 10 needs WM_MOUSEMOVE + popup "
        "polling that's known fragile; layer-1 toolTip() getter "
        "pins the contract. Tracking on #374."
    )
    try:
        _uia.close_action_dialog(_dlg_kb)
    except Exception:
        pass

    # ════════════════════════════════════════════════════════════════════
    # End Wave 11 probes
    # ════════════════════════════════════════════════════════════════════

    print("scenario: s31_simple_mode_regex DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
