"""Probe: tree column headers ↔ Set-Action-by-Field dialog dropdown.

Would have caught #238 (Score / Lock / Resolution missing from Select
dialog field dropdown). Complements the static AST-based probe
``test_probe_select_dialog_exposes_every_filterable_tree_column``
(layer 1, in ``tests/test_ui_probes.py``) — the static probe pins
the source-code invariant; this live probe verifies the *running app*
exposes the same set, catching shape drift the static probe can't
see (translation regressions, runtime filtering on certain fields,
mid-init mutations).

Behaviour (per #243 design comment):
  1. Launch the app, load a near-duplicates fixture.
  2. Read result-tree column headers via UIA → ``column_headers``.
  3. Open Action menu → "Set Action by Field…".
  4. Read the field dropdown's items via UIA ItemContainer pattern
     → ``dropdown_fields`` (walks the full virtualized list — past
     Qt's ``maxVisibleItems`` cap; see s50 history for why
     ``descendants(control_type="ListItem")`` doesn't suffice).
  5. Diff and emit ``probe_status: PASS|FAIL``.

Exit code: 0 on PASS, 1 on FAIL or runtime error.

Run:
    .venv/Scripts/python.exe -m qa.probes.field_dropdown_inventory
"""
from __future__ import annotations

import sys

from qa.probes._runtime import app_with_manifest
from qa.scenarios import _uia

FIXTURE_SOURCES = ["qa/sandbox/near-duplicates"]

# Probes are exploratory. The Decision/Action header pair below would
# both come up as "missing from dropdown" if treated naively — but
# they're intentionally non-filterable through the regex/Field dialog
# (the dialog SETS decisions; it doesn't filter by them). Translations
# render both keys as "Action" so the diff would also fire a spurious
# "duplicate" warning. Keeping the exclusion list explicit + commented
# so a future column that's intentionally non-filterable doesn't break
# the probe.
EXCLUDED_COLUMNS: frozenset[str] = frozenset({
    # The Action column shows the *user's decision* (delete / keep /
    # remove from list); the dialog mutates decisions via its
    # action-combo, not by filtering on them.
})


def main() -> int:
    print("probe: field_dropdown_inventory")
    with app_with_manifest(FIXTURE_SOURCES) as win:
        print("step: read_column_headers")
        column_headers = _uia.read_column_headers(win)
        print(f"  column_headers={column_headers!r}")
        if not column_headers:
            print("FAIL: tree exposed zero column headers — UIA tree empty?")
            print("probe_status: FAIL")
            return 1

        print("step: open_action_by_regex_dialog")
        dlg, _ = _uia.open_action_by_regex_dialog(win)

        try:
            print("step: read_field_combo_items")
            field_combo = _uia._find_descendant_by_aid_suffix(
                dlg, "ComboBox", ".regexFieldCombo"
            )
            if field_combo is None:
                print("FAIL: regexFieldCombo not found in dialog")
                print("probe_status: FAIL")
                return 1
            dropdown_fields = _uia.read_combobox_items(field_combo)
            print(f"  dropdown_fields={dropdown_fields!r}")
        finally:
            _uia.close_action_dialog(dlg)

        column_set = set(column_headers) - EXCLUDED_COLUMNS
        dropdown_set = set(dropdown_fields)

        missing_from_dropdown = sorted(column_set - dropdown_set)
        extra_in_dropdown = sorted(dropdown_set - column_set)

        print(f"  missing_from_dropdown={missing_from_dropdown!r}")
        print(f"  extra_in_dropdown={extra_in_dropdown!r}")

        if missing_from_dropdown or extra_in_dropdown:
            print("probe_status: FAIL")
            return 1

        print("probe_status: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
