"""Web scenario s49 — auto-select after scan (#212 / #393).

Ported from qa/scenarios/s49_scan_auto_select.py (Qt UIA).

Qt intent:
  - Expand ScanDialog's "Advanced settings", assert the "Auto select after scan"
    checkbox defaults OFF, toggle it ON, scan qa/sandbox/near-duplicates.
  - The worker promotes the top-scored row per group to action="KEEP" + is_locked
    (the #393 keep+lock write) BEFORE the manifest write, leaving non-keepers
    untouched (user_decision="", is_locked=0). On the 5-file near-dups fixture
    (q95 is the deterministic top score) exactly one KEEP results.

Web slice:
  - The Advanced-settings section is a collapsible <details> (SCAN_ADVANCED); the
    auto-select checkbox (SCAN_AUTO_SELECT) lives inside, default OFF. The driver
    expands it, asserts the default, toggles it ON, then drives the scan and reads
    the result from GET /api/manifest (action / is_locked / user_decision are all
    serialised on FileRow).

Qt divergence:
  D1. The Qt #239 assertion that the keeper is VISUALLY selected in the result
      tree is NOT ported — the web ResultTree has no post-scan selection-highlight
      yet (deferred FE follow-up). The web port asserts the manifest-level
      contract (action=KEEP + is_locked on the keeper; non-keepers untouched),
      which is the load-bearing data signal; the highlight is a UX nicety.
  D2. Manifest read via GET /api/manifest JSON vs Qt SQLite.

Desktop source: qa/scenarios/s49_scan_auto_select.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs; q95 = top score)
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
from qa.web.testid_constants import SCAN_ADVANCED, SCAN_AUTO_SELECT

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_EXPECTED_KEEPER = "neardup_00_q95.jpg"


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _collect(manifest: dict) -> dict[str, dict]:
    """Return {basename: {action, user_decision, is_locked}} across all groups."""
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = {
                "action": it.get("action", "") or "",
                "user_decision": it.get("user_decision", "") or "",
                "is_locked": bool(it.get("is_locked")),
            }
    return out


def run(*, base_url: str) -> None:
    """Enable auto-select in Advanced settings, scan, assert KEEP+lock on q95."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s49_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)

            # Expand Advanced settings; assert auto-select defaults OFF.
            page.get_by_test_id(SCAN_ADVANCED).click()
            auto = page.get_by_test_id(SCAN_AUTO_SELECT)
            auto.wait_for(state="visible", timeout=5_000)
            assert not auto.is_checked(), (
                "Auto-select checkbox must default OFF (opt-in)."
            )
            auto.click()  # toggle ON
            assert auto.is_checked(), "Auto-select did not turn ON after click."

            add_scan_source(page, _NEAR_DUPS_DIR, idx=0)
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)

            rows = _collect(_get_manifest(base_url, db_path))
            print(f"probe_status: s49 rows={ {k: rows[k] for k in sorted(rows)} }")

            assert len(rows) == 5, f"Expected 5 near-dup rows, got {sorted(rows)}"
            assert _EXPECTED_KEEPER in rows, (
                f"Expected keeper {_EXPECTED_KEEPER!r} missing from the manifest."
            )

            # Keeper: action=KEEP, is_locked, canonical-empty decision (#393/#425).
            keeper = rows[_EXPECTED_KEEPER]
            assert keeper["action"] == "KEEP", (
                f"{_EXPECTED_KEEPER}.action={keeper['action']!r}, expected 'KEEP' "
                f"(top-scored row must be promoted by auto-select)."
            )
            assert keeper["is_locked"] is True, (
                f"{_EXPECTED_KEEPER}.is_locked={keeper['is_locked']!r}, expected True "
                f"(#393 keep+lock write missing)."
            )
            assert keeper["user_decision"] == "", (
                f"{_EXPECTED_KEEPER}.user_decision={keeper['user_decision']!r}, "
                f"expected '' (canonical keep state — #425)."
            )

            # Exactly one KEEP (top-1-per-group on a single 5-row group).
            keep_count = sum(1 for r in rows.values() if r["action"] == "KEEP")
            assert keep_count == 1, (
                f"{keep_count} KEEP rows; expected exactly 1 (top-1-per-group)."
            )

            # Non-keepers: not KEEP, not locked, not decided (non-aggressive mode).
            for name, r in rows.items():
                if name == _EXPECTED_KEEPER:
                    continue
                assert r["action"] != "KEEP", (
                    f"Non-top row {name} has action=KEEP — only the top-scored row "
                    f"should be promoted."
                )
                assert r["is_locked"] is False, (
                    f"Non-keeper {name}.is_locked={r['is_locked']!r}, expected False "
                    f"(only the keeper is locked in non-aggressive mode)."
                )
                assert r["user_decision"] == "", (
                    f"Non-keeper {name}.user_decision={r['user_decision']!r}, "
                    f"expected '' (non-aggressive mode must not touch non-keepers)."
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
