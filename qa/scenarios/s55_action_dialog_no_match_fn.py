"""Scenario 55 — ActionDialog C1: no-match_fn informational placeholder (#347, #396).

Required source: qa/sandbox/unique (10 truly-unique JPEGs, no SHA256 or
pHash collisions → scanner produces zero dedup groups).

What this exercises (the production wiring that layer-1 unit tests
can't reach):

  1. Scan over unique-only sources produces a manifest with 10 rows but
     zero duplicate groups.
  2. dialog_handler.SetActionByFieldHandler resolves ``match_fn=None``
     when ``records_provider`` returns no groups
     (``if groups: match_fn = build_match_fn(groups)``).
  3. ActionDialog's C1 branch fires (#396 redesign): the Simple section
     stays visible but enters the **informational placeholder** state:
       - ``regexSimpleOpCombo`` is disabled (UIA-observable)
       - ``regexSimpleText`` is disabled (UIA-observable)
       - ``regexSimpleDisabledNote`` Text is visible above with the
         translated explanation
     The Regex section stays fully interactive — it's the only path
     the user has on this entry point.

Layer 1 pins the constructor branch in
``tests/test_select_dialog.py::TestDualSectionAlwaysVisible``. This
driver pins the UIA-observable surface: the disabled state is
actually visible to the user, the note text reaches the screen, and
the Regex line edit is still interactive. Catches a regression where
``setEnabled(False)`` gets dropped from the Simple inputs, the note
gets hidden, or the Simple section is accidentally fully hidden
(which would silently lose the dual-section contract on this branch).

Non-destructive: no decisions are written; no files are deleted.
Closes #366 C1 sub-item — the no-groups gap that s31 (always loads a
manifest with groups post-scan) can't cover. Rewritten for the #396
dual-section view; the pre-#396 contract was "Simple radio disabled
+ Regex radio force-checked" which no longer exists.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_DIR = REPO / "qa" / "sandbox" / "unique"

# Expected note text (en). zh_TW fallback included for non-English runners.
# The Simple inputs sit beneath an italic muted-color QLabel; UIA exposes
# the text via window_text() on the LabelText control.
EXPECTED_NOTE_EN = "Write-through preview unavailable"
EXPECTED_NOTE_ZH = "即時預覽不可用"


def _manifest_summary() -> tuple[int, int]:
    """Return (total_rows, group_count) for the post-scan manifest."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM migration_manifest"
        ).fetchone()[0]
        # group_id is NULL for no-action rows. Distinct non-NULL
        # group_ids = dedup group count.
        groups = conn.execute(
            "SELECT COUNT(DISTINCT group_id) FROM migration_manifest "
            "WHERE group_id IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    return total, groups


def main() -> int:
    print("scenario: s55_action_dialog_no_match_fn")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: verify_no_groups_in_manifest")
    total, groups = _manifest_summary()
    print(f"  manifest_total_rows={total} group_count={groups}")
    if total == 0:
        print("FAIL: scan produced 0 rows — fixture or scan misconfigured")
        return 1
    if groups != 0:
        print(
            f"FAIL: scan produced {groups} groups; expected 0 — fixture is "
            f"not truly unique or scanner regressed on the no-dedup path"
        )
        return 1

    print("step: open_action_dialog_via_menu_with_no_groups")
    # Action menu items are gated by manifest-loaded (not by groups-present),
    # so they're enabled here even though dedup found nothing.
    action_items = _uia.probe_menu_items(win, _uia.MENU_ACTION)
    print(f"  action_menu_items={action_items}")
    gated_state = {title: enabled for title, enabled in action_items}
    if not gated_state.get(_uia.ACTION_BY_REGEX, False):
        print(
            f"FAIL: {_uia.ACTION_BY_REGEX!r} disabled after loading "
            f"unique-only manifest — menu gating regression (manifest IS "
            f"loaded; the gate is per-manifest not per-group)"
        )
        return 1

    action_dlg, _ = _uia.open_action_by_regex_dialog(win)
    time.sleep(0.4)

    # ---------- C1 contract: Simple inputs disabled, placeholder note visible ----------
    print("step: probe_c1_simple_inputs_disabled")
    simple_op = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexSimpleOpCombo"
    )
    simple_text = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexSimpleText"
    )
    if simple_op is None or simple_text is None:
        print(
            "probe_status: C1-simple-inputs-present FAIL — "
            "regexSimpleOpCombo or regexSimpleText not in UIA tree "
            "(both sections must always render after #396, even on the "
            "no-match_fn branch as an informational placeholder)"
        )
        _uia.close_action_dialog(action_dlg)
        return 1

    # Load-bearing: Simple op-combo is disabled.
    _op_enabled = simple_op.is_enabled()
    print(f"  probe_status: C1-simple-op-is-enabled={_op_enabled}")
    if _op_enabled:
        print(
            "probe_status: C1-simple-op-disabled FAIL — "
            "regexSimpleOpCombo is enabled in no-match_fn dialog; "
            "setEnabled(False) may have been dropped from the C1 "
            "placeholder branch of __init__"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    print("probe_status: C1-simple-op-disabled PASS")

    # Load-bearing: Simple text-edit is disabled.
    _text_enabled = simple_text.is_enabled()
    print(f"  probe_status: C1-simple-text-is-enabled={_text_enabled}")
    if _text_enabled:
        print(
            "probe_status: C1-simple-text-disabled FAIL — "
            "regexSimpleText is enabled in no-match_fn dialog; "
            "setEnabled(False) may have been dropped from the C1 "
            "placeholder branch of __init__"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    print("probe_status: C1-simple-text-disabled PASS")

    # Load-bearing: placeholder note is visible and carries the
    # translated text. Without the note, disabled inputs would silently
    # confuse the user — the dual-section contract on the no-match_fn
    # branch depends on this signal.
    print("step: probe_c1_placeholder_note_visible")
    note = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Text", ".regexSimpleDisabledNote"
    )
    if note is None:
        print(
            "probe_status: C1-placeholder-note-present FAIL — "
            "regexSimpleDisabledNote not in UIA tree (the explanatory "
            "note above the disabled Simple inputs is missing — users "
            "would see disabled inputs with no signal as to why)"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    _note_visible = note.is_visible()
    print(f"  probe_status: C1-note-is-visible={_note_visible}")
    if not _note_visible:
        print(
            "probe_status: C1-placeholder-note-visible FAIL — "
            "regexSimpleDisabledNote is hidden on the no-match_fn branch"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    _note_text = (note.window_text() or "").strip()
    print(f"  probe_status: C1-note-text={_note_text!r}")
    _note_ok = (
        EXPECTED_NOTE_EN in _note_text
        or EXPECTED_NOTE_ZH in _note_text
    )
    if not _note_ok:
        print(
            f"probe_status: C1-placeholder-note-text FAIL — "
            f"expected note containing {EXPECTED_NOTE_EN!r} (or zh "
            f"equivalent); got {_note_text!r}"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    print("probe_status: C1-placeholder-note-visible PASS")

    # Load-bearing: Regex line edit IS interactive — it's the only
    # path the user has on this branch. Verifies the dual-section
    # contract: Simple is an informational placeholder, Regex is
    # fully usable.
    print("step: probe_c1_regex_line_edit_interactive")
    regex_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexLineEdit"
    )
    if regex_edit is None:
        print(
            "probe_status: C1-regex-edit-present FAIL — "
            "regexLineEdit not in UIA tree"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    _regex_enabled = regex_edit.is_enabled()
    print(f"  probe_status: C1-regex-edit-is-enabled={_regex_enabled}")
    if not _regex_enabled:
        print(
            "probe_status: C1-regex-edit-interactive FAIL — "
            "regexLineEdit is disabled on the no-match_fn branch; "
            "this would leave the user with no interactive section "
            "at all (Simple disabled per C1 placeholder, Regex "
            "should remain the escape hatch)"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    print("probe_status: C1-regex-edit-interactive PASS")

    print("step: close_dialog")
    _uia.close_action_dialog(action_dlg)

    print("scenario: s55_action_dialog_no_match_fn DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
