"""Probe: Set-Action-by-Field dialog panel visibility per field (#301).

For every item in the dialog's Field combo, the dialog swaps which
right-side panel is visible. The mapping is driven by
``_field_panel_is_numeric()`` reading ``_NUMERIC_FIELDS`` in
``app/views/dialogs/select_dialog.py``:

  * Numeric field → numeric-condition panel visible, Simple + Regex
    sections hidden (#209 numeric flow).
  * Non-numeric field → both Simple AND Regex sections visible
    (the dual-section dropped-toggle view from #396 / #402), numeric
    panel hidden.

This probe drives the dialog through every combo item and asserts
visibility matches. Catches:

  * a new field added to ``_NUMERIC_FIELDS`` without the swap firing
    at the receiver,
  * a non-numeric field accidentally hiding Simple or Regex
    (post-#402 dual-section regression),
  * ``_apply_mode_visibility`` mis-routing or forgetting to re-show
    a section after a numeric → non-numeric transition.

Iterates per field: open dialog → select field → assert visibility →
close dialog. Per-field open/close isolates each transition so a
sticky widget from one field can't mask another field's bug.

Exit code: 0 on PASS, 1 on FAIL.

Run:
    .venv/Scripts/python.exe -m qa.probes.select_dialog_permutation
"""
from __future__ import annotations

import sys
import time

from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.keyboard import send_keys

from app.views.dialogs.select_dialog import _NUMERIC_FIELDS
from qa.probes._runtime import app_with_manifest
from qa.scenarios import _uia

FIXTURE_SOURCES = ["qa/sandbox/near-duplicates"]

# objectName values set in app/views/dialogs/select_dialog.py — these
# are the canonical anchors for the three swappable panel containers.
# Do NOT invent variations; if the source renames, surface a FAIL
# instead of guessing.
SIMPLE_SUFFIX = ".regexSimpleRow"
REGEX_SUFFIX = ".regexRegexRow"
NUMERIC_SUFFIX = ".numericConditionRow"


def _panel_visible(dlg: UIAWrapper, suffix: str) -> bool:
    """Return True iff a descendant of ``dlg`` has an automation_id
    ending in ``suffix`` AND ``is_visible()`` reports True.

    Qt drops hidden containers from the UIA tree, so a missing
    descendant is treated as "not visible" (same outcome from the
    user's perspective). Walks all descendants without filtering on
    control_type so QWidget → {Pane, Custom} mapping drift across
    pywinauto versions doesn't silently miss a match.
    """
    for d in dlg.descendants():
        try:
            aid = d.element_info.automation_id or ""
        except Exception:
            continue
        if not aid.endswith(suffix):
            continue
        return bool(d.is_visible())
    return False


def _select_combo_index(combo: UIAWrapper, target_idx: int) -> None:
    """Select the combo item at ``target_idx`` via expand + arrow keys.

    pywinauto's ``ComboBox.select(text)`` only reliably reaches items
    inside Qt's default ``maxVisibleItems=10`` window (see s50 for the
    same constraint with "Resolution" at index 10). Expanding the
    popup, jumping to top via Home, then sending ``target_idx`` Down
    strokes lands on any item regardless of position.
    """
    try:
        combo.expand()
    except Exception:
        combo.click_input()
    time.sleep(0.3)
    send_keys("{HOME}")
    time.sleep(0.1)
    for _ in range(target_idx):
        send_keys("{DOWN}")
        time.sleep(0.02)
    time.sleep(0.1)
    send_keys("{ENTER}")
    time.sleep(0.3)


def main() -> int:
    print("probe: select_dialog_permutation")
    print(f"  numeric_fields={sorted(_NUMERIC_FIELDS)!r}")

    t_overall = time.time()
    failures: list[str] = []
    fields: list[str] = []

    with app_with_manifest(FIXTURE_SOURCES) as win:
        print("step: read_field_list")
        dlg, _ = _uia.open_action_by_regex_dialog(win)
        try:
            field_combo = _uia._find_descendant_by_aid_suffix(
                dlg, "ComboBox", ".regexFieldCombo"
            )
            if field_combo is None:
                print("FAIL: regexFieldCombo not found in dialog")
                print("probe_status: FAIL")
                return 1
            fields = _uia.read_combobox_items(field_combo)
        finally:
            _uia.close_action_dialog(dlg)
        print(f"  fields={fields!r}")
        if not fields:
            print("FAIL: field combo exposed zero items")
            print("probe_status: FAIL")
            return 1

        for idx, field in enumerate(fields):
            t_field = time.time()
            is_numeric = field in _NUMERIC_FIELDS
            if is_numeric:
                expect = {"simple": False, "regex": False, "numeric": True}
            else:
                expect = {"simple": True, "regex": True, "numeric": False}
            print(
                f"step: assert idx={idx} field={field!r} "
                f"expected={expect!r}"
            )

            dlg, _ = _uia.open_action_by_regex_dialog(win)
            try:
                field_combo = _uia._find_descendant_by_aid_suffix(
                    dlg, "ComboBox", ".regexFieldCombo"
                )
                if field_combo is None:
                    msg = (
                        f"FAIL: {field} expected combo "
                        f"got regexFieldCombo missing after reopen"
                    )
                    print(msg)
                    failures.append(msg)
                    continue
                _select_combo_index(field_combo, idx)

                observed = {
                    "simple": _panel_visible(dlg, SIMPLE_SUFFIX),
                    "regex": _panel_visible(dlg, REGEX_SUFFIX),
                    "numeric": _panel_visible(dlg, NUMERIC_SUFFIX),
                }
                elapsed = time.time() - t_field
                print(f"  observed={observed!r} elapsed_s={elapsed:.2f}")

                if observed != expect:
                    msg = f"FAIL: {field} expected {expect!r} got {observed!r}"
                    print(msg)
                    failures.append(msg)
            finally:
                _uia.close_action_dialog(dlg)

    overall = time.time() - t_overall
    print(
        f"summary: fields={len(fields)} failures={len(failures)} "
        f"elapsed_s={overall:.2f}"
    )
    if failures:
        print("probe_status: FAIL")
        return 1
    print("probe_status: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
