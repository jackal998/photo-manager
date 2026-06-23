"""Web scenario s57 — auto-select aggressive mode (#393).

Ported from qa/scenarios/s57_scan_auto_select_aggressive.py (Qt UIA).

Qt intent:
  - Expand ScanDialog's "Advanced settings"; assert the "Also mark all other
    files for delete" sub-checkbox defaults OFF; enable the parent "Auto select
    after scan", then enable the aggressive sub-option; scan near-duplicates.
  - On completion the keeper (q95) gets user_decision="" + is_locked=1 (the s49
    contract), and EVERY non-keeper in the scored group gets
    user_decision="delete" but is_locked=0 (they stay editable through the
    standard Execute Action flow).

Web slice:
  - The aggressive sub-checkbox (SCAN_AGGRESSIVE_DELETE) is disabled until the
    parent auto-select (SCAN_AUTO_SELECT) is on. The driver expands Advanced,
    asserts the sub defaults OFF, enables the parent, enables the sub, scans, and
    reads user_decision / is_locked from GET /api/manifest.

Qt divergence:
  D1. Manifest read via GET /api/manifest JSON vs Qt SQLite.
  D2. No scan-log "Auto-select aggressive" probe (ScanProgress unmounts on SSE
      finished — fast-CI flake); the aggressive write is asserted from the
      manifest decisions instead.

Desktop source: qa/scenarios/s57_scan_auto_select_aggressive.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs; q95 = keeper)
"""
from __future__ import annotations

import json
import os
import shutil
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
from qa.web.testid_constants import (
    SCAN_ADVANCED,
    SCAN_AGGRESSIVE_DELETE,
    SCAN_AUTO_SELECT,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_EXPECTED_KEEPER = "neardup_00_q95.jpg"
_EXPECTED_NON_KEEPERS = {
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
}


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _collect(manifest: dict) -> dict[str, dict]:
    """Return {basename: {user_decision, is_locked}} across all groups."""
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = {
                "user_decision": it.get("user_decision", "") or "",
                "is_locked": bool(it.get("is_locked")),
            }
    return out


def run(*, base_url: str) -> None:
    """Enable aggressive auto-select, scan, assert keeper-lock + non-keeper deletes."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s57_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)

            # Expand Advanced settings; assert the aggressive sub defaults OFF.
            page.get_by_test_id(SCAN_ADVANCED).click()
            aggressive = page.get_by_test_id(SCAN_AGGRESSIVE_DELETE)
            aggressive.wait_for(state="visible", timeout=5_000)
            assert not aggressive.is_checked(), (
                "Aggressive sub-checkbox must default OFF (destructive, opt-in)."
            )

            # Enable parent auto-select, then the aggressive sub-option.
            auto = page.get_by_test_id(SCAN_AUTO_SELECT)
            auto.click()
            assert auto.is_checked(), "Parent auto-select did not turn ON."
            aggressive.click()
            assert aggressive.is_checked(), (
                "Aggressive sub-checkbox did not turn ON after the parent enabled it."
            )

            add_scan_source(page, _NEAR_DUPS_DIR, idx=0)
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)

            state = _collect(_get_manifest(base_url, db_path))
            print(f"probe_status: s57 state={ {k: state[k] for k in sorted(state)} }")

            assert len(state) == 5, f"Expected 5 near-dup rows, got {sorted(state)}"

            # Keeper: locked, canonical-empty keep decision.
            assert _EXPECTED_KEEPER in state, (
                f"Expected keeper {_EXPECTED_KEEPER!r} missing from the manifest."
            )
            keeper = state[_EXPECTED_KEEPER]
            assert keeper["user_decision"] == "", (
                f"{_EXPECTED_KEEPER}.user_decision={keeper['user_decision']!r}, "
                f"expected '' (canonical keep — #425)."
            )
            assert keeper["is_locked"] is True, (
                f"{_EXPECTED_KEEPER}.is_locked={keeper['is_locked']!r}, expected True "
                f"(#393 keep+lock write missing)."
            )

            # Every non-keeper: user_decision='delete', NOT locked.
            for name in _EXPECTED_NON_KEEPERS:
                assert name in state, (
                    f"Expected non-keeper {name!r} missing from the manifest."
                )
                r = state[name]
                assert r["user_decision"] == "delete", (
                    f"Non-keeper {name}.user_decision={r['user_decision']!r}, "
                    f"expected 'delete' (aggressive mode tags every non-keeper)."
                )
                assert r["is_locked"] is False, (
                    f"Non-keeper {name}.is_locked={r['is_locked']!r}, expected False "
                    f"(aggressive mode must NOT lock non-keepers — they stay editable)."
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
