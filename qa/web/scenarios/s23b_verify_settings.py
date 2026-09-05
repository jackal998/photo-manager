"""Web scenario s23b — ScanDialog source/output persistence: LOAD side (#678-E).

Ported from the Qt s23a_set_settings / s23b_verify_settings pair. s23b is the
read-back half: it verifies a fresh launch reloads the source list + output that
the save path persisted, and that pHash/color thresholds are NOT persisted.

Web adaptation (self-contained round-trip):
  The Qt batch restarts the whole app between scenarios, so Qt s23b relies on
  s23a's on-disk settings.json surviving the restart. The web batch runs every
  scenario against ONE long-lived server and resets sources/output BEFORE each
  scenario (qa/web/_batch.py::reset_scan_persistence), so s23b cannot rely on
  s23a's residue. Instead it does the FULL round-trip itself: drive the dialog
  to WRITE (Start Scan -> PATCH /api/settings), then ``page.reload()`` — the web
  analog of the Qt app restart — then reopen the dialog and assert the LOAD path
  (ScanDialog's load-on-open effect) rebuilt the rows from GET /api/settings.
  This is strictly stronger than reading s23a's residue and is robust against
  scenario ordering.

Thresholds-not-persisted: the web ScanDialog's Advanced section DOES expose
pHash/dHash/mean-color threshold number inputs (#736, "Grouping sensitivity"),
but they are sent per-scan in the WebScanRequest only — never through
patchSettings, and the /api/settings allowlist excludes the threshold keys. We
assert their absence from GET /api/settings to pin the Qt s23b FORBIDDEN_KEYS
contract. Because GET returns only allowlisted keys, this specifically catches
the realistic regression where a future PR BOTH allowlists AND persists a
threshold key — the path by which a threshold could leak back into the dialog.
Coverage for the inputs themselves (render/wiring into the POST /api/scan body)
lives in s17_scan_dialog_widgets.py.

Desktop source: qa/scenarios/s23b_verify_settings.py
Fixtures:       qa/sandbox/unique, qa/sandbox/near-duplicates (distinct basenames)
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    open_scan_dialog,
    set_output_path,
    start_scan,
    wait_manifest_loaded,
)
from qa.web.testid_constants import (
    SCAN_OUTPUT_PATH,
    scan_source_path_testid,
    scan_source_recursive_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_SRC_UNIQUE = str(_REPO / "qa" / "sandbox" / "unique")
_SRC_NEARDUPS = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_FORBIDDEN_KEYS = (
    "scan.phash_threshold",
    "scan.dhash_threshold",
    "scan.mean_color_threshold",
)


def _get_settings(base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/api/settings"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (loopback)
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    """Write sources via the dialog, reload, assert the dialog reloads them."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s23b_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── WRITE via the UI save path (Start Scan -> PATCH /api/settings) ──
            open_scan_dialog(page)
            add_scan_source(page, _SRC_UNIQUE, idx=0)
            add_scan_source(page, _SRC_NEARDUPS, idx=1)
            page.get_by_test_id(scan_source_recursive_testid(0)).click()  # unique -> recursive
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)

            # ── RELOAD = fresh launch (the web cross-launch boundary) ──
            page.reload()

            # ── READ via the UI load path (ScanDialog load-on-open) ──
            open_scan_dialog(page)
            # The 2nd source row only materialises after the async settings-load
            # rebuilds the list, so waiting for it confirms the load completed.
            page.get_by_test_id(scan_source_path_testid(1)).wait_for(
                state="visible", timeout=10_000
            )

            row0_path = page.get_by_test_id(scan_source_path_testid(0)).input_value()
            row1_path = page.get_by_test_id(scan_source_path_testid(1)).input_value()
            row0_rec = page.get_by_test_id(scan_source_recursive_testid(0)).is_checked()
            row1_rec = page.get_by_test_id(scan_source_recursive_testid(1)).is_checked()
            output_val = page.get_by_test_id(SCAN_OUTPUT_PATH).input_value()
            print(
                f"probe_status: s23b reloaded rows="
                f"[{row0_path!r}({row0_rec}), {row1_path!r}({row1_rec})] "
                f"output={output_val!r}"
            )

            # Assert by basename (robust to row order); recursive flag per path.
            loaded = {
                Path(p).name: rec
                for p, rec in ((row0_path, row0_rec), (row1_path, row1_rec))
                if p
            }
            assert "unique" in loaded and "near-duplicates" in loaded, (
                f"Reloaded dialog sources {[row0_path, row1_path]} are missing the "
                f"expected basenames (load-on-open did not rebuild the source list)."
            )
            assert loaded["unique"] is True, (
                f"unique recursive not reloaded as True (got {loaded['unique']!r}) — "
                f"the per-row recursive flag did not round-trip."
            )
            assert loaded["near-duplicates"] is False, (
                f"near-duplicates recursive not reloaded as False "
                f"(got {loaded['near-duplicates']!r})."
            )
            assert output_val == db_path, (
                f"Reloaded output={output_val!r}, expected {db_path!r} "
                f"(output path did not round-trip)."
            )

        # ── Thresholds NOT persisted (Qt s23b FORBIDDEN_KEYS parity) ──
        settings = _get_settings(base_url)
        for forbidden in _FORBIDDEN_KEYS:
            assert forbidden not in settings, (
                f"{forbidden!r} unexpectedly present in GET /api/settings — the web "
                f"allowlist must never persist scan thresholds (Qt s23b contract)."
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
