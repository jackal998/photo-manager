"""Scenario 50 — Select dialog: numeric panel reachable from main-window menu (#237).

Required source: qa/sandbox/near-duplicates (5 JPEGs — same fixture as s43).

What this exercises (the bug #237 fixed by dialog_handler.py passing
``groups=`` through to ActionDialog):

  1. Open the standalone Set Action by Field/Regex dialog via the
     main-window menu route (Action → Set Action by Field/Regex…).
  2. Pick a numeric-capable field (Size (Bytes)) from the field combo.
  3. Assert the numeric-condition panel (>=/</== threshold + Top-N)
     actually surfaces — specifically, the ``numericValueEdit`` widget
     becomes findable. Before #237 landed, ``self._groups`` stayed
     empty because the main-window callsite never passed groups=, so
     ``_field_panel_is_numeric()`` returned False and the regex panel
     stayed visible regardless of which field the user picked.

Distinct from s43 (same numeric panel, but reached via Execute Action
dialog → Select by Field/Regex button — that callsite has always
passed ``groups=``). s43 is the *apply* coverage; s50 is the
*reachability from the second entry point* coverage.

Non-destructive: only checks the panel is reachable, then closes the
dialog. No Apply, no decisions written.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
SIZE_FIELD = "Size (Bytes)"


def main() -> int:
    print("scenario: s50_select_numeric_panel_from_main_window")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # 1. Scan so a manifest is loaded — the menu item is gated on it
    # (#244) and the dialog needs groups to drive the numeric panel.
    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    # 2. Open the Select dialog via the main-window menu — the path
    # whose missing ``groups=`` was #237.
    print("step: open_action_dialog_via_menu")
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_BY_REGEX)
    action_hwnd = _uia.wait_for_dialog(
        pid, _uia.ACTION_DIALOG_TITLE, timeout=5,
    )
    action_dlg = _uia.connect_by_handle(action_hwnd)
    _uia._focus(action_dlg)
    time.sleep(0.3)

    print("step: locate_field_combo")
    field_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexFieldCombo"
    )
    if field_combo is None:
        print("FAIL: regexFieldCombo not found in action dialog")
        return 1

    # #238 — Score / Lock / Resolution were missing from the dropdown
    # despite being visible columns in the tree. Probe their presence
    # by selecting each in turn; pywinauto raises when the requested
    # item isn't in the combo. en-locale labels are the same as the
    # internal field names (verified via translations/en.yml).
    print("step: assert_new_fields_in_field_combo")
    for new_field in ("Score", "Lock", "Resolution"):
        try:
            field_combo.select(new_field)
        except Exception as exc:
            print(
                f"FAIL: field {new_field!r} not selectable in the field "
                f"combo — likely missing from `fields` list in "
                f"dialog_handler.show_action_dialog. See #238. ({exc!r})"
            )
            return 1
        time.sleep(0.15)
    print("  fields_present=['Score', 'Lock', 'Resolution']")

    # 3. Pick the numeric field. _on_field_changed should swap the
    # regex panel out and the numeric panel in.
    print(f"step: select_numeric_field field={SIZE_FIELD!r}")
    field_combo.select(SIZE_FIELD)
    time.sleep(0.3)

    # 4. The probe: numericValueEdit must be findable AND visible.
    # Findable-but-hidden is the bug state before #237 — the widgets
    # exist in the layout but the parent container is hidden because
    # ``_field_panel_is_numeric()`` returned False.
    print("step: assert_numeric_panel_visible")
    value_edit = _uia._find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".numericValueEdit"
    )
    if value_edit is None:
        print(
            "FAIL: numericValueEdit widget not found after selecting "
            f"{SIZE_FIELD!r} — the main-window callsite likely dropped "
            "groups= when constructing ActionDialog (see #237)."
        )
        return 1
    try:
        visible = bool(value_edit.is_visible())
    except Exception as exc:
        print(f"FAIL: numericValueEdit visibility probe raised {exc!r}")
        return 1
    print(f"  numericValueEdit.is_visible={visible}")
    if not visible:
        print(
            "FAIL: numericValueEdit exists but is hidden — the numeric "
            "panel container did not surface. _groups is likely empty; "
            "see #237."
        )
        return 1

    # Also confirm the comparison-operator combo is there — both
    # widgets need to be reachable to call the panel functional.
    cmp_combo = _uia._find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".numericCmpCombo"
    )
    if cmp_combo is None or not bool(cmp_combo.is_visible()):
        print(
            "FAIL: numericCmpCombo not found or not visible — numeric "
            "panel only partially surfaced. See #237."
        )
        return 1

    # 5. Close the dialog without applying anything.
    print("step: close_dialog_no_apply")
    close_btn = _uia._find_dialog_button(action_dlg, _uia.ACTION_DIALOG_BTN_CLOSE)
    close_btn.click_input()
    time.sleep(0.3)

    print("scenario: s50_select_numeric_panel_from_main_window DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
