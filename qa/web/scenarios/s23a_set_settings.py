"""Web scenario s23a — ScanDialog source/output persistence: WRITE side (#678-E).

Ported from the Qt s23a_set_settings / s23b_verify_settings pair, which pins
that ScanDialog._save_to_settings persists the source list (path + per-row
recursive flag) and the output path, while pHash/color thresholds are
intentionally NOT persisted.

Web split (mirrors the Qt split, adapted to the web batch):
  - s23a (this) = the WRITE side. Drive the dialog (2 sources, one with the
    Recursive flag toggled, an output path), click Start Scan — the web analog
    of Qt's _save_to_settings, which fires from ScanDialog.handleStart via
    PATCH /api/settings — then assert GET /api/settings reflects the 2 persisted
    entries (per-row recursive flags) and the output path.
  - s23b = the LOAD side: a full UI write -> page.reload() -> UI read-back
    round-trip (the web's reload is the cross-launch boundary).

Why a real scan: like Qt s23a, the only save path is Start Scan. The scan over
qa/sandbox/{unique,near-duplicates} is acceptable noise (its manifest lands in
an isolated tmpdir). The per-scenario settings reset in qa/web/_batch.py
(reset_scan_persistence) keeps this scenario's persisted sources from leaking
into later scan scenarios — the web analog of the Qt batch's per-scenario
``configure`` step.

Desktop source: qa/scenarios/s23a_set_settings.py
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
from qa.web.testid_constants import scan_source_recursive_testid

_REPO = Path(__file__).resolve().parents[3]
_SRC_UNIQUE = str(_REPO / "qa" / "sandbox" / "unique")
_SRC_NEARDUPS = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def _get_settings(base_url: str) -> dict:
    url = f"{base_url.rstrip('/')}/api/settings"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (loopback)
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    """Mutate sources/output in the dialog, Start Scan, assert the persisted shape."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s23a_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)

            # Two sources (distinct basenames -> distinct auto-labels). Row 0
            # (unique) gets its Recursive flag toggled ON; row 1 (near-duplicates)
            # stays at the web blankSource default (OFF). This pins the per-row
            # recursive round-trip — the Qt s23a mutation-2 contract.
            add_scan_source(page, _SRC_UNIQUE, idx=0)
            add_scan_source(page, _SRC_NEARDUPS, idx=1)
            page.get_by_test_id(scan_source_recursive_testid(0)).click()
            assert page.get_by_test_id(scan_source_recursive_testid(0)).is_checked(), (
                "Recursive toggle on row 0 (unique) did not turn ON."
            )

            set_output_path(page, db_path)
            start_scan(page)
            # handleStart fires the persist (PATCH /api/settings) BEFORE the scan
            # worker starts; waiting for the manifest both lets the worker finish
            # cleanly and guarantees the fire-and-forget PATCH has landed.
            wait_manifest_loaded(page, timeout=120_000)

        # ── Assert the WRITE side: GET /api/settings reflects the mutations ──
        settings = _get_settings(base_url)
        persisted = settings.get("sources.list")
        print(
            f"probe_status: s23a persisted sources.list={persisted} "
            f"output={settings.get('sources.output')!r}"
        )

        assert isinstance(persisted, list) and len(persisted) == 2, (
            f"Expected 2 persisted sources, got {persisted!r}"
        )
        by_name = {
            Path(e["path"]).name: e
            for e in persisted
            if isinstance(e, dict) and e.get("path")
        }
        assert "unique" in by_name, f"'unique' source missing from {persisted!r}"
        assert "near-duplicates" in by_name, (
            f"'near-duplicates' source missing from {persisted!r}"
        )
        assert by_name["unique"].get("recursive") is True, (
            f"unique.recursive={by_name['unique'].get('recursive')!r}, "
            f"expected True (we toggled row 0)."
        )
        assert by_name["near-duplicates"].get("recursive") is False, (
            f"near-duplicates.recursive={by_name['near-duplicates'].get('recursive')!r}, "
            f"expected False (web default, untoggled)."
        )
        assert settings.get("sources.output") == db_path, (
            f"sources.output={settings.get('sources.output')!r}, expected {db_path!r}."
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
