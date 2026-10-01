"""Web scenario s02 — Empty folder: scan a freshly-created empty directory.

Ported from the desktop s02_empty_folder driver.

Qt intent:
  - Scan a source with zero image files.
  - Confirm the scan completes without error.
  - Confirm the result tree is empty (0 file rows).
  - Confirm the status-bar returns to an idle/empty-state baseline.

Web-observable assertions:
  An empty folder triggers the ``completed_empty`` SSE event: NO manifest is
  written or loaded (outputPath stays null), and — Qt ``_on_completed_empty``
  parity, #896 — the scan dialog STAYS OPEN with the no-files message and
  Start Scan enabled, so the user sees why nothing loaded and can retry.
  0. A fresh source row's "recursive" checkbox is checked by default (#896;
     Qt ``add_entry(recursive=True)``). The batch resets ``sources.list``
     before every scenario, so row 0 is the dialog's own blank row.
  1. ``scan-empty-message`` becomes visible while ``scan-dialog`` stays open.
  2. ``scan-start-button`` is visible and enabled (retry without closing).
  3. ``main-empty-state`` is still visible and ``count_file_rows == 0`` —
     no manifest was loaded.
  4. Escape closes the dialog; reopening shows no stale no-files message
     (closing reset the scan, so the settings load re-arms — #661 Bug 2).

Qt divergences:
  - Qt checks that ``scanProgressFrame`` is hidden after an empty scan
    (#510 regression guard) — no equivalent DOM element in the web UI;
    the entire ScanProgress component unmounts, so absence is implicit.
  - Qt asserts the Close button receives focus (#86) — focus management
    is not observable via Playwright in headless mode; omitted.
  - Qt showed the pipeline's log line in its log box; the web dialog shows
    one localized ``web.scan.empty_result`` line instead (the log box is
    part of the running-state panel, which unmounts at the end of a scan).
"""
from __future__ import annotations

import os
import tempfile

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    count_file_rows,
    open_scan_dialog,
    set_output_path,
    start_scan,
)
from qa.web.testid_constants import (
    MAIN_EMPTY_STATE,
    SCAN_DIALOG,
    SCAN_EMPTY_MESSAGE,
    SCAN_START_BUTTON,
    scan_source_recursive_testid,
)


def run(*, base_url: str) -> None:
    """Scan an empty temp directory and assert 0 file rows result."""
    with tempfile.TemporaryDirectory() as empty_dir:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
            db_path = db_f.name
        try:
            with PWContext(base_url=base_url) as ctx:
                page = ctx.new_page()
                page.goto("/")

                # Drive the scan manually. An EMPTY folder produces the
                # `completed_empty` terminal event: NO manifest is written or
                # loaded, and the dialog stays open (#896). run_scan()'s
                # "N groups · M files" wait never fires here — assert the
                # empty outcome.
                open_scan_dialog(page)

                # 0. #896 — a fresh row includes subfolders by default.
                default_box = page.get_by_test_id(scan_source_recursive_testid(0))
                assert default_box.is_checked(), (
                    "a fresh scan source row's recursive checkbox is unchecked "
                    "— a folder whose photos sit in subfolders would scan 0 files"
                )

                # Keep the default scope (recursive) — the user's path.
                add_scan_source(page, empty_dir, idx=0, recursive=True)
                set_output_path(page, db_path)
                start_scan(page)

                # 1. Terminal signal: the no-files message, in the open dialog.
                dialog = page.get_by_test_id(SCAN_DIALOG)
                page.get_by_test_id(SCAN_EMPTY_MESSAGE).wait_for(
                    state="visible", timeout=60_000
                )
                assert dialog.is_visible(), (
                    "the scan dialog closed after an empty scan — the user is "
                    "left at 'Ready' with nothing saying the scan found no files"
                )

                # 2. Start Scan is back and enabled, so the user can retry.
                start_btn = page.get_by_test_id(SCAN_START_BUTTON)
                assert start_btn.is_visible() and start_btn.is_enabled(), (
                    "Start Scan is not available after an empty scan"
                )

                # 3. No manifest loaded → still the empty state, zero rows.
                page.locator(f'[data-testid="{MAIN_EMPTY_STATE}"]').wait_for(
                    state="visible", timeout=10_000
                )
                file_row_count = count_file_rows(page)
                assert file_row_count == 0, (
                    f"Expected 0 file rows after scanning an empty folder, "
                    f"got {file_row_count}"
                )

                # 4. Escape closes it; a reopen must not show the stale message.
                page.keyboard.press("Escape")
                dialog.wait_for(state="hidden", timeout=10_000)
                open_scan_dialog(page)
                assert page.get_by_test_id(SCAN_EMPTY_MESSAGE).count() == 0, (
                    "reopening the scan dialog still shows the previous "
                    "no-files message — closing did not reset the scan"
                )
                page.keyboard.press("Escape")
                dialog.wait_for(state="hidden", timeout=10_000)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass
