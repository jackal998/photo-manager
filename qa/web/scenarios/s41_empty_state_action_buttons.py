"""Web scenario s41 — empty-state action buttons.

Ported from qa/scenarios/s41_empty_state_action_buttons.py (Qt UIA).

Qt intent:
  - When no manifest is loaded, the main content area is not a bare
    placeholder — it offers action affordances so a first-time user has an
    obvious next step (Scan, or Open an existing manifest).

Web-observable assertions:
  - On a fresh page (no manifest), ``main-empty-state`` is visible and contains
    a Scan button (``main-empty-scan``) and an Open-Manifest button
    (``main-empty-open``).
  - Clicking Scan opens the scan dialog (``scan-dialog``).
  - Clicking Open Manifest opens the filesystem picker (``fs-browser``).

Qt divergences:
  - Qt asserts the buttons live inside the QStackedWidget empty page; the web
    asserts the same affordances by testid inside ``main-empty-state``.
"""
from __future__ import annotations

from qa.web._pw import PWContext
from qa.web.testid_constants import (
    FS_BROWSER,
    FS_BROWSER_CANCEL,
    MAIN_EMPTY_OPEN,
    MAIN_EMPTY_SCAN,
    MAIN_EMPTY_STATE,
    SCAN_DIALOG,
)


def run(*, base_url: str) -> None:
    """Assert the empty state exposes Scan + Open affordances that open dialogs."""
    with PWContext(base_url=base_url) as ctx:
        page = ctx.new_page()
        page.goto("/")

        # Empty state is shown with both action buttons.
        page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(state="visible", timeout=10_000)
        scan_btn = page.get_by_test_id(MAIN_EMPTY_SCAN)
        open_btn = page.get_by_test_id(MAIN_EMPTY_OPEN)
        assert scan_btn.is_visible(), "empty-state Scan button is missing"
        assert open_btn.is_visible(), "empty-state Open Manifest button is missing"

        # Scan button opens the scan dialog; Escape closes it.
        scan_btn.click()
        page.get_by_test_id(SCAN_DIALOG).wait_for(state="visible", timeout=5_000)
        page.keyboard.press("Escape")
        page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=5_000)

        # Open Manifest button opens the filesystem picker; Cancel closes it.
        page.get_by_test_id(MAIN_EMPTY_OPEN).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="visible", timeout=5_000)
        page.get_by_test_id(FS_BROWSER_CANCEL).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="hidden", timeout=5_000)
