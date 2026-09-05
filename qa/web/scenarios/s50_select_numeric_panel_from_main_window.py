"""Web scenario s50 — open the Action dialog (numeric panel) from the menu bar.

Ported from qa/scenarios/s50_select_numeric_panel_from_main_window.py (Qt UIA).

Qt intent:
  - Open the Action ("Set Action by Regex") dialog from the main window's menu
    and reach the numeric condition panel for a numeric field.

Web slice:
  - The menu bar's Action menu is the SECOND ActionDialog entry point (the
    toolbar button is the first). Its items are manifest-gated — disabled until
    a manifest is loaded — mirroring the Qt ``MANIFEST_ACTIONS`` gate.
  - After a scan, Action → Set Action by Regex opens the ActionDialog; selecting
    a numeric field (Score) reveals the numeric condition panel.

Qt divergences:
  - Qt's keyboard-driven menu traversal and in-dialog focus probes have no web
    equivalent; the menu and field combo are driven by testid. The numeric
    panel's threshold/Top-N mechanics are covered by s43/s56, not re-asserted
    here — this scenario's job is the menu → dialog → numeric-panel path.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    ACTION_DIALOG,
    ACTION_FIELD_COMBO,
    ACTION_NUMERIC_ROW,
    FS_BROWSER,
    FS_BROWSER_CANCEL,
    MENU_ACTION,
    MENU_ACTION_SET,
    MENU_FILE,
    MENU_FILE_OPEN,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def run(*, base_url: str) -> None:
    """Verify the Action menu gate, then open ActionDialog + numeric panel via menu."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s50_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Before any manifest: Action → Set Action is disabled.
            page.get_by_test_id(MENU_ACTION).click()
            set_item = page.get_by_test_id(MENU_ACTION_SET)
            set_item.wait_for(state="visible", timeout=5_000)
            assert set_item.get_attribute("data-disabled") is not None, (
                "Action → Set Action must be disabled with no manifest loaded"
            )
            page.keyboard.press("Escape")  # close the menu

            # Scan to load a manifest.
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # Now the item is enabled and opens the ActionDialog (2nd entry point).
            page.get_by_test_id(MENU_ACTION).click()
            set_item = page.get_by_test_id(MENU_ACTION_SET)
            set_item.wait_for(state="visible", timeout=5_000)
            assert set_item.get_attribute("data-disabled") is None, (
                "Action → Set Action must be enabled once a manifest is loaded"
            )
            set_item.click()
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=5_000)

            # Selecting a numeric field reveals the numeric condition panel.
            page.get_by_test_id(ACTION_FIELD_COMBO).select_option(label="Score")
            page.get_by_test_id(ACTION_NUMERIC_ROW).wait_for(state="visible", timeout=5_000)
            page.keyboard.press("Escape")  # close the action dialog
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="hidden", timeout=5_000)

            # The File menu's Open Manifest also drives the FsBrowser file
            # picker (same affordance as the empty-state Open). Drive it once
            # from the menu bar to cover the File-menu E2E path; Cancel out.
            page.get_by_test_id(MENU_FILE).click()
            page.get_by_test_id(MENU_FILE_OPEN).click()
            page.get_by_test_id(FS_BROWSER).wait_for(state="visible", timeout=5_000)
            page.get_by_test_id(FS_BROWSER_CANCEL).click()
            page.get_by_test_id(FS_BROWSER).wait_for(state="hidden", timeout=5_000)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
