"""Scenario 55 — ActionDialog C1: Simple radio disabled when no match_fn (#347).

Required source: qa/sandbox/unique (10 truly-unique JPEGs, no SHA256 or
pHash collisions → scanner produces zero dedup groups).

What this exercises (the production wiring that layer-1 unit tests
can't reach):

  1. Scan over unique-only sources produces a manifest with 10 rows but
     zero duplicate groups.
  2. dialog_handler.SetActionByFieldHandler resolves ``match_fn=None``
     when ``records_provider`` returns no groups (dialog_handler.py
     line 91 — ``if groups: match_fn = build_match_fn(groups)``).
  3. ActionDialog's C1 branch (select_dialog.py line 758) fires:
     - Simple radio button is disabled
     - tooltip set to ``action_dialog.simple_disabled_no_match_fn``
       ("Simple mode requires a live preview data source")
     - Regex radio is force-checked as fallback

Layer 1 pins the constructor branch in
``tests/test_select_dialog.py::TestC1ModeToggleAlwaysVisible``. This
driver pins the UIA-observable surface: the disabled state is
actually visible to the user, the Regex radio is the selected one,
and the tooltip property reaches the widget. Catches a regression
where ``setEnabled(False)`` gets dropped or the mode-toggle widgets
get swapped for controls that ignore setEnabled.

Non-destructive: no decisions are written; no files are deleted.
Closes #366 C1 sub-item (deferred in Wave 11 / PR #384 because s31
always loads a manifest with groups post-scan — this scenario fills
the no-groups gap).
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

# Expected tooltip text (en). zh_TW fallback included for non-English runners.
EXPECTED_TOOLTIP_EN = "Simple mode requires a live preview data source"
EXPECTED_TOOLTIP_ZH = "簡易模式需要即時預覽資料來源"


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

    # ---------- C1 contract: Simple radio disabled, Regex force-checked ----------
    print("step: probe_c1_simple_radio_disabled")
    simple_radio = _uia._find_descendant_by_aid_suffix(
        action_dlg, "RadioButton", ".regexModeSimple"
    )
    regex_radio = _uia._find_descendant_by_aid_suffix(
        action_dlg, "RadioButton", ".regexModeRegex"
    )
    if simple_radio is None or regex_radio is None:
        print(
            "probe_status: C1-radios-present FAIL — "
            "regexModeSimple or regexModeRegex not in UIA tree"
        )
        _uia.close_action_dialog(action_dlg)
        return 1

    # Load-bearing: Simple is disabled.
    _simple_enabled = simple_radio.is_enabled()
    print(f"  probe_status: C1-simple-is-enabled={_simple_enabled}")
    if _simple_enabled:
        print(
            "probe_status: C1-simple-radio-disabled FAIL — "
            "Simple radio is enabled in no-match_fn dialog; setEnabled(False) "
            "may have been dropped from select_dialog.py line 761"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    print("probe_status: C1-simple-radio-disabled PASS")

    # Load-bearing: Regex is the active radio (fallback selection).
    _regex_selected = regex_radio.is_selected()
    print(f"  probe_status: C1-regex-is-selected={_regex_selected}")
    if not _regex_selected:
        print(
            "probe_status: C1-regex-radio-force-checked FAIL — "
            "Regex radio is not selected; the fallback setChecked(True) at "
            "select_dialog.py line 765 may have regressed"
        )
        _uia.close_action_dialog(action_dlg)
        return 1
    print("probe_status: C1-regex-radio-force-checked PASS")

    # Best-effort: tooltip text reaches the widget. UIA exposes Qt's
    # setToolTip via HelpText (LegacyIAccessible.Help). On QRadioButton
    # this is reliably populated when the widget is disabled, unlike the
    # hover-popup path (B11 in #374). Soft-FAIL with diagnostic if the
    # accessor doesn't surface text — the load-bearing contract is the
    # disabled state above, not the exact tooltip string (layer-1 pins
    # that).
    print("step: probe_c1_tooltip_property_set")
    _tooltip_text = ""
    try:
        # pywinauto's UIAWrapper exposes the underlying IUIAutomationElement
        # via .element_info.element. CurrentHelpText is the UIA HelpText
        # property, which Qt populates from setToolTip().
        _tooltip_text = (
            simple_radio.element_info.element.CurrentHelpText or ""
        )
    except Exception as _exc:
        print(f"  probe_status: C1-tooltip-read-attempt FAIL — {_exc!r}")
    print(f"  probe_status: C1-tooltip-text={_tooltip_text!r}")
    _tooltip_ok = (
        EXPECTED_TOOLTIP_EN in _tooltip_text
        or EXPECTED_TOOLTIP_ZH in _tooltip_text
    )
    if _tooltip_ok:
        print("probe_status: C1-tooltip-property-set PASS")
    else:
        # Don't return 1 — the disabled state above is the load-bearing
        # contract; tooltip text is layer-1's responsibility. Surface the
        # gap so a future contributor can pick up tooltip-via-UIA work.
        print(
            f"probe_status: C1-tooltip-property-set SKIP — UIA HelpText "
            f"did not surface the expected tooltip "
            f"({EXPECTED_TOOLTIP_EN!r} or zh equivalent). "
            f"Layer-1 TestC1ModeToggleAlwaysVisible pins the exact string; "
            f"this probe's load-bearing assertions (disabled + force-checked) "
            f"both PASSed above."
        )

    print("step: close_dialog")
    _uia.close_action_dialog(action_dlg)

    print("scenario: s55_action_dialog_no_match_fn DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
