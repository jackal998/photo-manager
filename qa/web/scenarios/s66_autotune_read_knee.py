"""Web scenario s66 — read-knee autotune default-ON / opt-out (#551 Phase 3+4).

Ported from qa/scenarios/s66_autotune_read_knee.py (Qt UIA).

Qt intent:
  - The "Auto-tune reader concurrency" checkbox in ScanDialog's Advanced settings
    starts ON by default (#551 Phase 4 flipped it to opt-out).
  - Running a real scan with autotune at its default-ON state does NOT change the
    grouping: the 5 near-duplicates still collapse into exactly one non-null
    group, identical to the autotune-OFF result. Reader concurrency never affects
    group assignment (idx is threaded through read → hash → classify; #526/#538).

Web slice:
  - Expand Advanced settings (SCAN_ADVANCED), assert the autotune checkbox
    (SCAN_AUTOTUNE) is checked by default, scan near-duplicates leaving autotune
    ON, then assert via GET /api/manifest that the 5 files form exactly one group.

Qt divergence:
  D1. Grouping read via GET /api/manifest JSON (total_groups / total_files / a
      single group's items) vs Qt's SQLite group_id read. The web grouped
      manifest only surfaces multi-member groups, so "5 files in 1 group" is
      total_groups==1 + total_files==5 + the lone group holding all 5.
  D2. No scan-log 'Done.' probe (ScanProgress unmounts on SSE finished); the
      manifest-loaded status bar is the completion signal (run helper).

Desktop source: qa/scenarios/s66_autotune_read_knee.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs → one near-dup group)
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
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
from qa.web.testid_constants import SCAN_ADVANCED, SCAN_AUTOTUNE

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    """Assert autotune defaults ON, scan, and verify grouping is unchanged."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)

            # Expand Advanced settings; assert autotune defaults ON (opt-out).
            page.get_by_test_id(SCAN_ADVANCED).click()
            autotune = page.get_by_test_id(SCAN_AUTOTUNE)
            autotune.wait_for(state="visible", timeout=5_000)
            assert autotune.is_checked(), (
                "Auto-tune checkbox must default ON (#551 Phase 4 opt-out)."
            )

            # Scan leaving autotune at its default-ON state.
            add_scan_source(page, _NEAR_DUPS_DIR, idx=0)
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)

            manifest = _get_manifest(base_url, db_path)
            groups = manifest.get("groups", [])
            print(
                f"probe_status: s66 total_groups={manifest.get('total_groups')} "
                f"total_files={manifest.get('total_files')} "
                f"group_sizes={[len(g.get('items', [])) for g in groups]}"
            )

            # The 5 near-duplicates must collapse into exactly one non-null group.
            assert manifest.get("total_files") == 5, (
                f"Expected 5 near-duplicate rows, got "
                f"total_files={manifest.get('total_files')}."
            )
            assert len(groups) == 1, (
                f"autotune-ON broke grouping: expected exactly 1 group, got "
                f"{len(groups)} ({[g.get('group_number') for g in groups]}). "
                f"Reader concurrency must never change group assignment "
                f"(#551 / #526/#538)."
            )
            only_group = groups[0]
            assert len(only_group.get("items", [])) == 5, (
                f"The single group must hold all 5 near-duplicates, got "
                f"{len(only_group.get('items', []))} items."
            )
            assert only_group.get("group_number") is not None, (
                "The near-dup group has a null group_number."
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
