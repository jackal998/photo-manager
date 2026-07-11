"""Web scenario s21 — List menu → Remove from List (menu-bar path, #678-D).

Ported from qa/scenarios/s21_list_menu_remove.py (Qt UIA).

Qt intent:
  Drive the MENU-BAR entry (MainWindow._remove_from_list_toolbar) — distinct
  from s20's right-click context-menu path — across three branches:
  (a) no selection → "No items selected" QMessageBox, manifest unchanged;
  (b) single highlight → row removed, outcome='ignored';
  (c) multi highlight → both rows removed, others untouched.

Web slice (#678-D — the List menu was the last unshipped MenuBar entry):
  - New top-level "List" menu (``menu-list``) with "Remove from List"
    (``menu-list-remove-from-list``), wired to the SAME store.removeFromList
    path the context menu uses (finalize outcome='ignored', lock-aware 409).
  - Deliberate web variant of branch (a), asserted here: instead of Qt's
    "No items selected" QMessageBox the item is DISABLED without a
    selection — the same gating idiom the web already uses for
    "Execute (only selected)" (s44).
  - Branches (b)/(c) keep Qt's semantics: rows leave the review tree
    immediately (finalize outcome='ignored' — verified BOTH via manifest
    absence and a direct sqlite read of the outcome column), files stay on
    disk, unselected rows keep their staged decisions.

Desktop source: qa/scenarios/s21_list_menu_remove.py
Fixture:        qa/sandbox/near-duplicates (5 files, one near-dup group)
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_context_item,
    click_row,
    ctrl_click_row,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    MENU_LIST,
    MENU_LIST_REMOVE_FROM_LIST,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

_Q95 = "neardup_00_q95.jpg"
_Q88 = "neardup_01_q88.jpg"
_Q80 = "neardup_02_q80.jpg"
_Q72 = "neardup_03_q72.jpg"
_Q65 = "neardup_04_q65.jpg"
_ALL = (_Q95, _Q88, _Q80, _Q72, _Q65)


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _basename_decisions(manifest: dict) -> dict[str, str]:
    """{basename: user_decision} for every VISIBLE row (outcome='' load)."""
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            out[Path(item["file_path"]).name] = item.get("user_decision", "") or ""
    return out


def _await_removed(
    base_url: str,
    db_path: str,
    targets: tuple[str, ...],
    *,
    timeout_s: float = 20.0,
) -> dict[str, str]:
    """Poll until every target basename has left the visible manifest."""
    deadline = time.monotonic() + timeout_s
    decisions: dict[str, str] = {}
    while time.monotonic() < deadline:
        decisions = _basename_decisions(_get_manifest(base_url, db_path))
        if not (set(targets) & set(decisions)):
            return decisions
        time.sleep(0.5)
    raise AssertionError(
        f"Timed out waiting for {targets} to leave the manifest; "
        f"still visible: {sorted(set(targets) & set(decisions))}"
    )


def _sqlite_outcomes(db_path: str, basenames: tuple[str, ...]) -> dict[str, str]:
    """Read the persisted outcome column directly (finalize proof)."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT source_path, outcome FROM migration_manifest"
        ).fetchall()
    finally:
        con.close()
    wanted = set(basenames)
    return {
        Path(fp).name: (outcome or "")
        for fp, outcome in rows
        if Path(fp).name in wanted
    }


def _open_list_menu_item(page):
    """Open the List menu and return the Remove-from-List item locator."""
    page.get_by_test_id(MENU_LIST).click()
    item = page.get_by_test_id(MENU_LIST_REMOVE_FROM_LIST)
    item.wait_for(state="visible", timeout=5_000)
    return item


def run(*, base_url: str) -> None:
    """Gate on selection (web variant of Qt's QMessageBox), then remove a
    single row and a multi-selection via the menu-bar entry."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s21_")
    db_path = os.path.join(tmpdir, "s21.db")
    try:
        for name in _ALL:
            src = _SRC_DIR / name
            assert src.exists(), f"near-duplicates fixture missing: {src}"
            shutil.copy(str(src), os.path.join(tmpdir, name))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the tmpdir copy ────────────────────────────────
            run_scan(page, sources=[tmpdir], output_path=db_path, scan_timeout=60_000)
            decisions = _basename_decisions(_get_manifest(base_url, db_path))
            assert set(decisions) == set(_ALL), (
                f"Expected all 5 fixture rows in one manifest, got {sorted(decisions)}"
            )
            gid = None
            manifest = _get_manifest(base_url, db_path)
            for group in manifest["groups"]:
                names = {Path(i["file_path"]).name for i in group["items"]}
                if _Q95 in names:
                    gid = str(group["group_number"])
            assert gid is not None

            # ── Branch A (web variant): no selection → item DISABLED ───────
            item = _open_list_menu_item(page)
            assert item.get_attribute("data-disabled") is not None, (
                "List → Remove from List must be DISABLED with no main-tree "
                "selection (web variant of Qt's 'No items selected' QMessageBox)"
            )
            page.keyboard.press("Escape")
            print("probe_status: s21 branch_a disabled-without-selection OK")

            # Stage decisions on two rows so branch C can prove untouched rows
            # keep them (mirrors Qt s21's 'others unchanged' assertions).
            for name in (_Q72, _Q65):
                right_click_row(page, row_file_testid(gid, name))
                click_context_item(page, CTX_SET_ACTION_DELETE)

            # ── Branch B: single selection → removed, finalized 'ignored' ──
            click_row(page, row_file_testid(gid, _Q95))
            item = _open_list_menu_item(page)
            assert item.get_attribute("data-disabled") is None, (
                "List → Remove from List must be ENABLED once a row is selected"
            )
            item.click()
            post_b = _await_removed(base_url, db_path, (_Q95,))
            assert os.path.exists(os.path.join(tmpdir, _Q95)), (
                "branch B: removed row's FILE must stay on disk "
                "(remove-from-list finalizes outcome='ignored', never deletes)"
            )
            outcomes = _sqlite_outcomes(db_path, (_Q95,))
            assert outcomes.get(_Q95) == "ignored", (
                f"branch B: persisted outcome must be 'ignored', got "
                f"{outcomes.get(_Q95)!r}"
            )
            print(f"probe_status: s21 branch_b removed={_Q95} outcome=ignored")

            # ── Branch C: multi selection → both removed, others untouched ─
            click_row(page, row_file_testid(gid, _Q88))
            ctrl_click_row(page, row_file_testid(gid, _Q80))
            item = _open_list_menu_item(page)
            item.click()
            post_c = _await_removed(base_url, db_path, (_Q88, _Q80))
            outcomes = _sqlite_outcomes(db_path, (_Q88, _Q80))
            for name in (_Q88, _Q80):
                assert os.path.exists(os.path.join(tmpdir, name)), (
                    f"branch C: {name} left the list but its file was deleted"
                )
                assert outcomes.get(name) == "ignored", (
                    f"branch C: {name} persisted outcome must be 'ignored', "
                    f"got {outcomes.get(name)!r}"
                )
            # Untouched rows keep their staged 'delete' decisions.
            for name in (_Q72, _Q65):
                assert post_c.get(name) == "delete", (
                    f"branch C disturbed {name}: {post_c.get(name)!r} "
                    f"(expected staged 'delete' to survive)"
                )
            print("probe_status: s21 branch_c multi-remove OK, others untouched")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
