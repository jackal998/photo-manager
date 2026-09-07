"""Web scenario s02 — Empty folder: scan a freshly-created empty directory.

Ported from qa/scenarios/s02_empty_folder.py (Qt UIA).

Qt intent:
  - Scan a source with zero image files.
  - Confirm the scan completes without error.
  - Confirm the result tree is empty (0 file rows).
  - Confirm the status-bar returns to an idle/empty-state baseline.

Web-observable assertions:
  An empty folder triggers the ``completed_empty`` SSE event: the scan
  dialog closes but NO manifest is written or loaded (outputPath stays
  null), so the app stays in the empty state.
  1. The scan dialog (``scan-dialog``) auto-closes on completion.
  2. ``main-empty-state`` is still visible — no manifest was loaded.
  3. ``count_file_rows(page) == 0`` — no file rows in the result tree.

Qt divergences:
  - Qt checks that ``scanProgressFrame`` is hidden after an empty scan
    (#510 regression guard) — no equivalent DOM element in the web UI;
    the entire ScanProgress component unmounts, so absence is implicit.
  - Qt asserts the Close button receives focus (#86) — focus management
    is not observable via Playwright in headless mode; omitted.
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
from qa.web.testid_constants import MAIN_EMPTY_STATE, SCAN_DIALOG


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
                # `completed_empty` terminal event: the scan dialog closes but
                # NO manifest is written or loaded (outputPath stays null), so
                # the app stays in the empty state. run_scan()'s "N groups ·
                # M files" wait never fires here — assert the empty outcome.
                open_scan_dialog(page)
                add_scan_source(page, empty_dir, idx=0)
                set_output_path(page, db_path)
                start_scan(page)

                # Terminal signal: the scan dialog auto-closes on completion.
                page.locator(f'[data-testid="{SCAN_DIALOG}"]').wait_for(
                    state="hidden", timeout=60_000
                )

                # No manifest loaded → the app remains in the empty state with
                # zero result rows.
                page.locator(f'[data-testid="{MAIN_EMPTY_STATE}"]').wait_for(
                    state="visible", timeout=10_000
                )
                file_row_count = count_file_rows(page)
                assert file_row_count == 0, (
                    f"Expected 0 file rows after scanning an empty folder, "
                    f"got {file_row_count}"
                )
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass
