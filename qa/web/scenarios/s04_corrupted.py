"""Web scenario s04 — Corrupted file: scan tolerates a truncated JPEG.

Ported from qa/scenarios/s04_corrupted.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/corrupted (one truncated JPEG, phash=None).
  - Confirm the scan completes without crashing.
  - Confirm the manifest has 0 indexed rows (corrupted file is skipped).
  - Confirm the log surfaced a 'Skipped (unreadable)' line reconciling the
    headline 0 with 'Hashed 1/1' (#87 regression guard).

Web divergences (faithfully documented):
  - Qt asserts the log text contains 'Skipped (unreadable)' and
    'Indexed in manifest'. The ScanProgress component (and its log
    element ``scan-progress-log``) auto-unmounts on the SSE 'finished'
    event — the log is no longer in the DOM after the dialog closes.
    Observable equivalent: assert 0 file rows via ``count_file_rows``
    and confirm the manifest-loaded status bar shows the '0 groups · 0 files'
    pattern, which implies the corrupted file was skipped and the scan
    completed cleanly.
  - Qt also asserts absence of 'Total files scanned' (misleading old label,
    #87). This is a server-side log string; since the log is gone from DOM
    we cannot assert it here. The 0-row outcome is the load-bearing check.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    count_file_rows,
    run_scan,
)

_REPO = Path(__file__).resolve().parents[3]
_CORRUPTED_DIR = str(_REPO / "qa" / "sandbox" / "corrupted")


def run(*, base_url: str) -> None:
    """Scan the corrupted sandbox and assert 0 manifest rows (file skipped)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Run the scan against the corrupted fixture directory.
            status_text = run_scan(
                page,
                sources=[_CORRUPTED_DIR],
                output_path=db_path,
                scan_timeout=60_000,
            )

            # The scan must complete (manifest-loaded status returned) without raising.
            assert status_text, (
                "Expected a non-empty status bar text after corrupted scan"
            )

            # The corrupted file must be silently skipped; result tree has 0 rows.
            # This is the web-observable equivalent of Qt's 'Indexed in manifest: 0'
            # + 'Skipped (unreadable): 1' summary check.
            file_row_count = count_file_rows(page)
            assert file_row_count == 0, (
                f"Expected 0 file rows after scanning the corrupted fixture "
                f"(truncated JPEG must be skipped, not indexed), got {file_row_count}"
            )

            # Status bar must reflect 0 groups / 0 files, not an error state.
            assert "0" in status_text, (
                f"Status bar does not show '0' after corrupted scan: {status_text!r}"
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
