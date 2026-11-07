"""Web scenario s81 — the auto-select rule toggles (PR-4a, PRD v2 §6 / §11 item 4).

WEB-ONLY. There is no Qt driver: the desktop client was removed in #646 and
never had rule toggles. The layer-1 contracts (each rule's blocker, the trace,
the classify flags) are pinned by tests/test_auto_select.py::TestRuleToggles,
tests/test_dedup_rule_toggles.py and tests/test_scan_runner.py. What only a
live run settles is the whole path: the Rules block persists its toggles, the
scan reads them, and the manifest shows the decision each rule made.

Fixtures (the same two sources as s80):
  - qa/sandbox/near-duplicates/ — REVIEW_DUPLICATE rows, plus possibly one
    EXACT re-encode.
  - a temp folder holding two BYTE copies of qa/sandbox/unique/unique_00.jpg.

Phase 1 — defaults, aggressive, confirm threshold "medium":
  1. Every rule checkbox reads checked (the defaults; B7 is ON, today's
     behaviour).
  2. The copy pair has one KEEP and one delete.
  3. No REVIEW_DUPLICATE row is marked delete: B8 vetoes what medium admits.
  4. Soft probe: print every near-dup row's confidence_level. If no REVIEW row
     is medium, print probe_status "s81 B8 unproven" and do not fail; the
     dHash agreement is platform-dependent (PR-3 QA note).

Phase 2 — a second scan into a new manifest, B3 and B8 off:
  5. The confirm threshold still reads "medium" (persisted).
  6. The copy pair has one KEEP and zero deletes: B3 spares the EXACT copy.
  7. No EXACT row anywhere is marked delete.
  8. Positive control: every non-keeper REVIEW row at medium is marked delete.
     This is the #517 set that B8 off restores. Soft when no medium row exists.

Phase 3 — reopen the dialog:
  9. B3 and B8 read unchecked (persisted); B4, B5 and B7 read checked.

Cleanup restores the rule defaults and the "high" threshold, then removes the
temp folder.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import expect

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
    SCAN_CONFIRM_BELOW,
    SCAN_RESCAN_CONFIRM_DIALOG,
    SCAN_RESCAN_CONFIRM_DISCARD,
    SCAN_RULE_B3,
    SCAN_RULE_B4,
    SCAN_RULE_B5,
    SCAN_RULE_B7,
    SCAN_RULE_B8,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_UNIQUE_JPEG = _REPO / "qa" / "sandbox" / "unique" / "unique_00.jpg"
_COPY_NAMES = ("copy_a.jpg", "copy_b.jpg")
_DEFAULT_RULES = {"B3": True, "B4": True, "B5": True, "B7": True, "B8": True}


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _patch_settings(base_url: str, updates: dict) -> None:
    body = json.dumps({"updates": updates}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/settings",
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        resp.read()


def _rows_by_name(manifest: dict) -> dict[str, dict]:
    """Return {basename: {group, action, user_decision, confidence_level}}."""
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = {
                "group": str(group["group_number"]),
                "action": it.get("action", "") or "",
                "user_decision": it.get("user_decision", "") or "",
                "confidence_level": it.get("confidence_level"),
            }
    return out


def _make_copy_folder(tmpdir: str) -> str:
    """Two byte-identical copies of one unique JPEG (EXACT by pHash and bytes)."""
    folder = os.path.join(tmpdir, "copies")
    os.makedirs(folder)
    for name in _COPY_NAMES:
        shutil.copyfile(_UNIQUE_JPEG, os.path.join(folder, name))
    return folder


def _scan(page, base_url: str, tmpdir: str, db_path: str) -> dict[str, dict]:
    """Fill both sources and the output path, start, and read the manifest."""
    add_scan_source(page, _NEAR_DUPS_DIR, idx=0)
    add_scan_source(page, _make_copy_folder(tmpdir), idx=1)
    set_output_path(page, db_path)
    start_scan(page)
    if page.get_by_test_id(SCAN_RESCAN_CONFIRM_DIALOG).is_visible():
        page.get_by_test_id(SCAN_RESCAN_CONFIRM_DISCARD).click()
    wait_manifest_loaded(page, timeout=120_000)
    return _rows_by_name(_get_manifest(base_url, db_path))


def _check_phase1(rows: dict[str, dict]) -> None:
    copies = [rows[n] for n in _COPY_NAMES if n in rows]
    assert len(copies) == 2, f"Expected 2 copy rows, got {sorted(rows)}"
    keeps = [r for r in copies if r["action"] == "KEEP"]
    deletes = [r for r in copies if r["user_decision"] == "delete"]
    assert len(keeps) == 1, f"copy pair must have exactly one KEEP: {copies!r}"
    assert len(deletes) == 1, f"copy pair must have exactly one delete: {copies!r}"
    for name, row in rows.items():
        if row["action"] == "REVIEW_DUPLICATE":
            assert row["user_decision"] != "delete", (
                f"B8 on at medium must spare REVIEW_DUPLICATE: {name} {row!r}"
            )


def _check_phase2(rows: dict[str, dict]) -> None:
    copies = [rows[n] for n in _COPY_NAMES if n in rows]
    assert len(copies) == 2, f"Expected 2 copy rows, got {sorted(rows)}"
    keeps = [r for r in copies if r["action"] == "KEEP"]
    deletes = [r for r in copies if r["user_decision"] == "delete"]
    assert len(keeps) == 1, f"B3 off keeps the score-pick KEEP stamp: {copies!r}"
    assert not deletes, f"B3 off must leave the copy pair undecided: {copies!r}"
    for name, row in rows.items():
        if row["action"] == "EXACT":
            assert row["user_decision"] != "delete", (
                f"B3 off: no EXACT row may be marked delete: {name} {row!r}"
            )
    medium = [
        (name, row) for name, row in rows.items()
        if row["action"] == "REVIEW_DUPLICATE" and row["confidence_level"] == "medium"
    ]
    if not medium:
        print("probe_status: s81 B8-off control unproven (no medium REVIEW row on this platform)")
        return
    for name, row in medium:
        assert row["user_decision"] == "delete", (
            f"B8 off at medium must restore the #517 set: {name} {row!r}"
        )


def run(*, base_url: str) -> None:
    """Toggle the rules, scan twice, and check each rule's effect in the manifest."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s81_")
    first_db = os.path.join(tmpdir, "first.db")
    second_db = os.path.join(tmpdir, "second.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Phase 1 — defaults, aggressive, medium.
            open_scan_dialog(page)
            page.get_by_test_id(SCAN_ADVANCED).click()
            for testid in (SCAN_RULE_B3, SCAN_RULE_B4, SCAN_RULE_B5, SCAN_RULE_B7, SCAN_RULE_B8):
                expect(page.get_by_test_id(testid)).to_have_attribute("data-state", "checked")
            page.get_by_test_id(SCAN_AUTO_SELECT).click()
            page.get_by_test_id(SCAN_AGGRESSIVE_DELETE).click()
            page.get_by_test_id(SCAN_CONFIRM_BELOW).select_option("medium")
            rows1 = _scan(page, base_url, tmpdir, first_db)
            print(f"probe_status: s81 phase1 rows={ {k: rows1[k] for k in sorted(rows1)} }")
            _check_phase1(rows1)
            if not any(
                r["confidence_level"] == "medium" and r["action"] == "REVIEW_DUPLICATE"
                for r in rows1.values()
            ):
                print("probe_status: s81 B8 unproven (no medium REVIEW row on this platform)")

            # Phase 2 — second scan, B3 and B8 off.
            open_scan_dialog(page)
            page.get_by_test_id(SCAN_ADVANCED).click()
            page.get_by_test_id(SCAN_AUTO_SELECT).click()
            page.get_by_test_id(SCAN_AGGRESSIVE_DELETE).click()
            expect(page.get_by_test_id(SCAN_CONFIRM_BELOW)).to_have_value("medium")
            page.get_by_test_id(SCAN_RULE_B3).click()
            page.get_by_test_id(SCAN_RULE_B8).click()
            rows2 = _scan(page, base_url, tmpdir, second_db)
            _check_phase2(rows2)

            # Phase 3 — reopen; the toggles persisted.
            open_scan_dialog(page)
            page.get_by_test_id(SCAN_ADVANCED).click()
            expect(page.get_by_test_id(SCAN_RULE_B3)).to_have_attribute("data-state", "unchecked")
            expect(page.get_by_test_id(SCAN_RULE_B8)).to_have_attribute("data-state", "unchecked")
            expect(page.get_by_test_id(SCAN_RULE_B4)).to_have_attribute("data-state", "checked")
            expect(page.get_by_test_id(SCAN_RULE_B5)).to_have_attribute("data-state", "checked")
            expect(page.get_by_test_id(SCAN_RULE_B7)).to_have_attribute("data-state", "checked")
    finally:
        try:
            _patch_settings(
                base_url,
                {"scan.auto_select.rules": _DEFAULT_RULES, "scan.auto_select.confirm_below": "high"},
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
