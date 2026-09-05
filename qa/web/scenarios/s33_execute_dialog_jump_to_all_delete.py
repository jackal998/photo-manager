"""Web scenario s33 — Execute dialog: all-delete warning banner + jump link (#166).

Ported from qa/scenarios/s33_execute_dialog_jump_to_all_delete.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files, one group).
  - Bulk-delete all five rows via ActionDialog regex "neardup_" matching all basenames.
  - Open Execute Action dialog.
  - Assert the all-delete warning banner (QLabel) is visible and its text contains
    at least one digit (the group number rendered as an anchor).
  - Close (no execute).
  - NOTE: Qt could not click the anchor inside the QLabel reliably on hosted CI
    runners because pywinauto UIA doesn't expose first-class elements for
    QLabel <a> children (see gotcha #1 in the Qt scenario comments).

Web slice:
  1. run_scan seeds a fresh temp manifest (db_path in tempfile.mkdtemp()).
  2. GET /api/manifest snapshots the single group's group_number and all 5 basenames.
  3. For each of the 5 basenames: right_click_row + click CTX_SET_ACTION_DELETE.
  4. GET /api/manifest asserts all 5 rows carry user_decision == 'delete'.
  5. open_execute_dialog; wait for EXECUTE_DIALOG visible.
  6. Assert EXECUTE_ALL_DELETE_BANNER is visible (server-side condition: every row
     in the execute scope has decision='delete').
  7. Derive the jump-link testid via execute_all_delete_jump_testid(group_number).
     Assert the button is visible and click it.  No exception == interactive.
  8. page.keyboard.press('Escape'); wait for EXECUTE_DIALOG hidden.
  9. GET /api/manifest asserts all 5 rows still carry user_decision == 'delete'
     (Close is non-destructive; no execute was triggered).
  10. finally: shutil.rmtree(tmpdir).

Qt divergences:
  - NON-DESTRUCTIVE: this scenario never calls POST /api/execute, so there is no
    need to copy the fixture into a tmpdir.  The scan is done against
    qa/sandbox/near-duplicates/ directly (read-only).  Only the manifest .db is
    written into a tempdir.
  - Qt derives decisions from SQLite directly.  The web port uses GET /api/manifest.
  - Qt's all-delete bulk step uses mark_all_via_regex_standalone (ActionDialog UIA
    helper).  The web port drives it file-by-file via right-click context menu — the
    same DB write path, no regex dialog needed here.
  - Qt can only assert the banner TEXT contains a digit (pywinauto can't click
    QLabel <a> anchors on CI).  The web IMPROVES on Qt: the jump button is a real
    <button> with a data-testid, so we assert it is visible AND click it — verifying
    the interactive element is mounted, not just rendered text.
  - Post-Close assertion: Qt re-reads SQLite after closing the dialog.  The web port
    re-GETs /api/manifest and confirms all 5 rows still have user_decision == 'delete'
    (absence-based contract: executed rows vanish from the manifest; a Close without
    Execute must leave all rows present with their pre-Open decisions intact).
  - Absence-based contract (load-bearing): after POST /api/execute, deleted rows
    disappear from GET /api/manifest because _LOAD_ALL_SQL filters on outcome = ''.
    This scenario never executes, so all 5 rows must still appear in the post-Close
    manifest — that is the correctness predicate.
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
    run_scan,
    right_click_row,
    click_context_item,
    open_execute_dialog,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    EXECUTE_DIALOG,
    EXECUTE_ALL_DELETE_BANNER,
    row_file_testid,
    execute_all_delete_jump_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# The 5 fixture basenames in qa/sandbox/near-duplicates/.
# Listing them explicitly keeps the scenario self-documenting and stable
# against accidental additions to the sandbox directory.
_FIXTURE_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Shape: {"groups": [{"group_number": N, "items": [{file_path, user_decision,
    score, is_locked, ...}]}], "total_groups": N, "total_files": M}.
    The key is "items", NOT "files".
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_decisions(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for all visible rows in the manifest.

    Only rows with outcome='' appear in GET /api/manifest (the visibility
    predicate in _LOAD_ALL_SQL).  Deleted/ignored rows vanish from the
    response entirely — see module docstring for the absence-based contract.
    """
    decisions: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            basename = Path(item["file_path"]).name
            decisions[basename] = item.get("user_decision", "") or ""
    return decisions


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan near-dups, set all-delete, verify banner + jump link, close non-destructively."""
    # NON-DESTRUCTIVE: scan qa/sandbox/near-duplicates/ directly (no file copies
    # needed because we never call POST /api/execute).  Only the manifest .db
    # lives in the tmpdir.
    tmpdir = tempfile.mkdtemp(prefix="qa_s33_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the near-duplicates fixture ──────────────────────
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 2: read the manifest to derive group_number ──────────────
            # group_number is the key used in row testids (str(group_number)).
            # Mirror s15: first_group["group_number"] cast to str.
            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, (
                f"Expected at least one group in the near-duplicates manifest, "
                f"got total_groups={manifest['total_groups']}"
            )
            first_group = manifest["groups"][0]
            group_number = str(first_group["group_number"])

            # Confirm all 5 fixture basenames are present before setting decisions.
            pre_decisions = _extract_decisions(manifest)
            assert len(pre_decisions) == 5, (
                f"Expected 5 fixture rows in manifest, got {len(pre_decisions)}: "
                f"{sorted(pre_decisions)}"
            )
            for basename in _FIXTURE_BASENAMES:
                assert basename in pre_decisions, (
                    f"Fixture basename {basename!r} missing from manifest rows "
                    f"{sorted(pre_decisions)}"
                )

            # ── Step 3: set each row to 'delete' via the right-click context menu ──
            # Driving one row at a time mirrors s15's approach and avoids any
            # dependency on the ActionDialog regex path.
            for basename in _FIXTURE_BASENAMES:
                row_tid = row_file_testid(group_number, basename)
                right_click_row(page, row_tid)
                click_context_item(page, CTX_SET_ACTION_DELETE)

            # ── Step 4: assert all 5 rows carry user_decision == 'delete' ─────
            post_decision_manifest = _get_manifest(base_url, db_path)
            post_decisions = _extract_decisions(post_decision_manifest)

            assert len(post_decisions) == 5, (
                f"Expected 5 rows after bulk-delete via context menu, "
                f"got {len(post_decisions)}: {sorted(post_decisions)}"
            )
            for basename in _FIXTURE_BASENAMES:
                decision = post_decisions.get(basename, "")
                assert decision == "delete", (
                    f"Expected user_decision='delete' for {basename!r} after "
                    f"context-menu set-delete, got {decision!r}"
                )

            # ── Step 5: open the execute dialog ──────────────────────────────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                state="visible", timeout=10_000
            )

            # ── Step 6: assert the all-delete warning banner is visible ───────
            # The banner fires when every row in the execute scope has
            # user_decision == 'delete' — all 5 do, so it must appear.
            banner = page.get_by_test_id(EXECUTE_ALL_DELETE_BANNER)
            banner.wait_for(state="visible", timeout=10_000)

            # ── Step 7: assert the jump link is visible and clickable ─────────
            # execute_all_delete_jump_testid(group_number) returns the testid
            # "execute-all-delete-jump-{group_number}" for the <button> that
            # scrolls the execute tree to the named group.
            # The web IMPROVES on Qt: this is a real <button> we can click,
            # not a QLabel <a> that UIA can't reach on CI.
            jump_tid = execute_all_delete_jump_testid(group_number)
            jump_btn = page.get_by_test_id(jump_tid)
            jump_btn.wait_for(state="visible", timeout=10_000)
            # Clicking the jump button must not raise (interactive element check).
            jump_btn.click()

            # ── Step 8: close the execute dialog with Escape ─────────────────
            # Escape dismisses the dialog without triggering POST /api/execute
            # (the non-destructive path).
            page.keyboard.press("Escape")
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                state="hidden", timeout=5_000
            )

            # ── Step 9: assert decisions unchanged after Close ────────────────
            # Close must not alter any row decisions.  All 5 rows must still be
            # present (outcome='' means they were never executed) with
            # user_decision == 'delete'.
            post_close_manifest = _get_manifest(base_url, db_path)
            post_close_decisions = _extract_decisions(post_close_manifest)

            assert len(post_close_decisions) == 5, (
                f"Expected 5 rows after closing execute dialog (non-destructive), "
                f"got {len(post_close_decisions)}: {sorted(post_close_decisions)}"
            )
            for basename in _FIXTURE_BASENAMES:
                decision = post_close_decisions.get(basename, "")
                assert decision == "delete", (
                    f"Close changed manifest: expected user_decision='delete' for "
                    f"{basename!r}, got {decision!r}. Close must not execute."
                )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
