"""Web scenario s16 — Open Manifest via the filesystem picker.

Ported from qa/scenarios/s16_open_manifest.py (Qt UIA).

Qt intent:
  - File → Open Manifest lets the user pick an existing manifest file from
    disk (a QFileDialog) and load it into the result tree.

Web-observable assertions:
  - Scan a fixture to produce a manifest ``.db`` in a dedicated temp dir.
  - Reload the page so the in-memory manifest is cleared (empty state).
  - Re-open that manifest through the FsBrowser file picker (driven from the
    empty-state Open button): the picker opens at the db's directory, the
    ``.db`` is flagged as a manifest, selecting it + Confirm loads it.
  - The post-reopen status bar matches the post-scan status exactly
    (same "N groups · M files").

Qt divergences:
  - Qt drives QFileDialog (a native OS dialog) which UIA can only partially
    inspect; the web picker is in-DOM and asserted by testid. The ``.db``
    extension (which the web fs-browse now flags as a manifest alongside
    ``.sqlite``) is exercised directly here.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import open_manifest_via_picker, run_scan
from qa.web.testid_constants import MAIN_EMPTY_STATE

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def run(*, base_url: str) -> None:
    """Scan → reload → reopen the manifest via the file picker; assert it loads."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s16_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Produce a manifest by scanning the near-duplicates fixture.
            status = run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )
            assert "groups" in status, f"scan did not load a manifest: {status!r}"
            assert os.path.exists(db_path), f"manifest file not written: {db_path}"

            # Reload clears the in-memory manifest → empty state.
            page.reload()
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(state="visible", timeout=10_000)

            # Re-open the manifest through the FsBrowser file picker.
            reopened = open_manifest_via_picker(page, db_path, timeout=30_000)
            assert reopened == status, (
                f"status after reopening via picker ({reopened!r}) "
                f"differs from post-scan status ({status!r})"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
