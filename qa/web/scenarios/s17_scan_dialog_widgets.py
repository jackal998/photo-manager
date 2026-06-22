"""Web scenario s17 — scan dialog browse widgets (nested filesystem picker).

Ported from qa/scenarios/s17_scan_dialog_widgets.py (Qt UIA).

Qt intent:
  - The scan dialog's Browse buttons open a file picker for the source folder
    and the output manifest; picking writes back into the corresponding field.

Web-observable assertions (the part most at risk — a nested modal):
  - Source-row Browse opens the FsBrowser (directory mode) *over* the still-open
    scan dialog. Crucially the scan dialog must NOT be dismissed by the nested
    layer. Confirming returns the folder into the source path field.
  - Output Browse opens the FsBrowser (save mode) with the filename prefilled;
    confirming writes ``<dir><sep><filename>`` into the output field.

Qt divergences:
  - Qt's native QFileDialog is replaced by the in-DOM FsBrowser. The picker is
    opened pre-positioned at the field's current directory (initialPath hint),
    so the test confirms the round-trip without deep filesystem navigation.
"""
from __future__ import annotations

import os
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import open_scan_dialog
from qa.web.testid_constants import (
    FS_BROWSER,
    FS_BROWSER_CONFIRM,
    FS_BROWSER_FILENAME,
    SCAN_DIALOG,
    SCAN_OUTPUT_BROWSE,
    SCAN_OUTPUT_PATH,
    scan_source_browse_testid,
    scan_source_path_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_SANDBOX_DIR = str(_REPO / "qa" / "sandbox")


def run(*, base_url: str) -> None:
    """Drive the scan dialog's Browse buttons through the nested picker."""
    with PWContext(base_url=base_url) as ctx:
        page = ctx.new_page()
        page.goto("/")
        open_scan_dialog(page)

        # --- Source browse (directory mode), opened at a known folder ---------
        # Seed the path field so the picker opens there (one-click confirm).
        src_path = page.get_by_test_id(scan_source_path_testid(0))
        src_path.fill(_NEAR_DUPS_DIR)
        page.get_by_test_id(scan_source_browse_testid(0)).click()

        # Nested picker must appear AND the scan dialog must stay open.
        page.get_by_test_id(FS_BROWSER).wait_for(state="visible", timeout=5_000)
        assert page.get_by_test_id(SCAN_DIALOG).is_visible(), (
            "scan dialog was dismissed when the nested picker opened"
        )

        # Confirm the current folder; picker closes, scan dialog survives.
        page.get_by_test_id(FS_BROWSER_CONFIRM).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="hidden", timeout=5_000)
        assert page.get_by_test_id(SCAN_DIALOG).is_visible(), (
            "scan dialog was dismissed after confirming the picker"
        )
        picked_src = src_path.input_value()
        assert picked_src.lower().endswith("near-duplicates"), (
            f"source path not written by picker: {picked_src!r}"
        )

        # --- Output browse (save mode), filename prefilled --------------------
        out_field = page.get_by_test_id(SCAN_OUTPUT_PATH)
        out_field.fill(os.path.join(_SANDBOX_DIR, "out.db"))
        page.get_by_test_id(SCAN_OUTPUT_BROWSE).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="visible", timeout=5_000)
        assert page.get_by_test_id(FS_BROWSER_FILENAME).input_value() == "out.db", (
            "save-mode filename was not prefilled from the output field"
        )
        page.get_by_test_id(FS_BROWSER_CONFIRM).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="hidden", timeout=5_000)
        picked_out = out_field.input_value()
        assert picked_out.endswith("out.db"), (
            f"output path not written by picker: {picked_out!r}"
        )

        # Clean up: close the dialog.
        page.keyboard.press("Escape")
        page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=5_000)
