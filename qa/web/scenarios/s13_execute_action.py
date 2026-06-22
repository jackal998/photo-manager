"""Web scenario s13 — Destructive execute round-trip (canonical Execute Action).

Ported from qa/scenarios/s13_execute_action.py (Qt UIA).

Qt intent:
  - Regenerate a disposable 5-JPEG near-duplicate fixture (random gradients
    at qualities 95/88/80/72/65) so the recycle bin doesn't accumulate identical
    content across runs.
  - Scan → close & load → open ExecuteActionDialog → mark ALL rows 'delete'
    via a "Set by Field / Regex" bulk-apply (field=File Name, regex=.+) → Apply
    → close inner dialog → click Execute → confirm "All Files Will Be Deleted" →
    Yes → verify (a) fixture files no longer exist on disk, (b) manifest rows
    carry executed=1 (checked via SQLite).

Web slice:
  1. Copy the 5 stable qa/sandbox/near-duplicates/*.jpg into a tmpdir to guard
     the repo fixture from real deletion.
  2. run_scan([tmpdir]) → fresh manifest.db under the same tmpdir.
  3. GET /api/manifest → assert 1 group, 5 items; extract group_id +
     5 basenames (mirrors s15 group_number extraction).
  4. Right-click each of the 5 file rows → CTX_SET_ACTION_DELETE via context menu.
  5. GET /api/manifest → assert every item has user_decision == 'delete'.
  6. open_execute_dialog(page) → wait EXECUTE_DIALOG visible.
  7. Click EXECUTE_BTN_EXECUTE → all-delete safety gate fires:
     wait EXECUTE_ALL_DELETE_CONFIRM visible.
  8. (Optional) Assert the confirm sheet body contains a digit (row count sanity).
  9. Click EXECUTE_ALL_DELETE_CONFIRM_YES.
  10. Poll GET /api/manifest until total_files==0 — the execute dialog does
      NOT auto-close on the web (the store keeps executeOpen=true and just
      refreshes the now-empty tree); the completion signal is the manifest
      emptying, not the dialog hiding.
  11. ASSERT absence-based (corrected — see divergence notes):
      (a) Every one of the 5 copied files is absent from disk (os.path.exists
          returns False) — files went to the OS recycle bin via send2trash.
      (b) GET /api/manifest shows total_files == 0 — the group vanished because
          all rows now have outcome='deleted', which is filtered out by the
          WHERE outcome='' predicate in ManifestRepository._LOAD_ALL_SQL.
  12. finally: shutil.rmtree(tmpdir) cleans up any file that somehow survived.

Qt divergences:
  - Qt regenerates its fixture from random gradients each run (guaranteeing
    pHash-cluster convergence across 5 retries).  The web port copies the
    stable qa/sandbox/near-duplicates/*.jpg (which already pass the scanner's
    near-duplicate threshold) to avoid the imagehash/numpy dependency and the
    random-retry logic; the scan outcome is identical.
  - Qt marks all rows via a bulk-regex "mark_all_via_regex" (ActionDialog
    field=File Name, regex=.+).  The web port drives the same result row-by-row
    via right-click context-menu CTX_SET_ACTION_DELETE.  Both paths exercise
    the same PATCH /api/decision write-path and produce user_decision='delete'
    on every row; the route through the UI differs.
  - Qt verifies post-execute state by checking a SQLite column (executed=1).
    The web port uses the CORRECTED ABSENCE-BASED assertion contract instead:
      * executed/outcome column check: NOT done — the manifest endpoint
        filters rows with WHERE outcome='' (ManifestRepository._LOAD_ALL_SQL
        line 60), so deleted rows are absent from the GET /api/manifest
        response.  Asserting item['outcome']=='deleted' on a visible item
        would be a vacuous assertion (it can never be true for a row that
        the endpoint returns).
      * Correct assertion: (a) file absent from disk, (b) total_files==0 in
        the manifest response (all rows gone).
  - Qt reads the file-system state after Execute completes by checking
    fixture_paths directly.  The web port checks copies in tmpdir — the
    actual near-duplicates sandbox files are never touched.
  - The Qt scenario uses qa/run-manifest.sqlite (a persistent shared manifest
    path). The web port creates a fresh manifest.db under tmpdir on every run,
    which avoids cross-run manifest state leakage and makes cleanup trivial.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
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
    EXECUTE_BTN_EXECUTE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    row_file_testid,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

# The 5 stable fixture basenames.  Listed explicitly so any addition /
# rename in the sandbox immediately causes a test failure rather than a
# silent mis-count.
_FIXTURE_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]


# ---------------------------------------------------------------------------
# HTTP helper (mirrors s31 / s51 / s56 — self-contained per convention)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Uses urllib (stdlib only) to keep the module dependency-free at import
    time, matching the convention established by s15/s31/s51/s56.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_decisions(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for all visible manifest items.

    Iterates group["items"] (the server-side serialisation key, NOT "files").
    Empty string means no decision set.  Only rows with outcome=='' appear
    in the manifest (WHERE outcome='' in _LOAD_ALL_SQL).
    """
    decisions: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            decisions[basename] = file_row.get("user_decision", "")
    return decisions


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Destructive execute round-trip: scan copies → mark all delete → Execute.

    Copies the 5 near-duplicate fixture JPEGs into an isolated tmpdir before
    scanning, so POST /api/execute deletes only the copies (which go to the
    OS recycle bin via send2trash) and never touches the repo sandbox files.
    """
    # ── Set up tmpdir with fresh copies of the fixture ───────────────────────
    tmpdir = tempfile.mkdtemp(prefix="qa_s13_")
    try:
        # Copy each fixture file into tmpdir.
        copied_paths: list[str] = []
        for basename in _FIXTURE_BASENAMES:
            src = _NEAR_DUPS_DIR / basename
            dst = os.path.join(tmpdir, basename)
            shutil.copy(str(src), dst)
            copied_paths.append(dst)

        db_path = os.path.join(tmpdir, "s13_manifest.db")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the tmpdir copies ───────────────────────────────
            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 2: read manifest; extract group_id + basenames ───────────
            # Mirror s15 exactly: first_group["group_number"] cast to str.
            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, (
                f"Expected at least 1 group after scanning near-duplicate "
                f"copies, got total_groups={manifest['total_groups']}"
            )
            first_group = manifest["groups"][0]
            group_id = str(first_group["group_number"])

            # Confirm all 5 basenames are present in the first group.
            present_basenames = [
                Path(f["file_path"]).name
                for f in first_group.get("items", [])
            ]
            assert len(present_basenames) == 5, (
                f"Expected 5 near-duplicate rows in group {group_id}, "
                f"got {len(present_basenames)}: {sorted(present_basenames)}"
            )

            # ── Step 3: mark each row 'delete' via the context menu ───────────
            # Drives the same write-path as s15 step (a), repeated for all 5.
            for basename in present_basenames:
                row_tid = row_file_testid(group_id, basename)
                right_click_row(page, row_tid)
                click_context_item(page, CTX_SET_ACTION_DELETE)

            # ── Step 4: verify all 5 items carry user_decision='delete' ───────
            post_decision = _get_manifest(base_url, db_path)
            decisions = _extract_decisions(post_decision)
            for basename in present_basenames:
                assert decisions.get(basename) == "delete", (
                    f"Pre-execute: expected user_decision='delete' for "
                    f"{basename!r}, got {decisions.get(basename)!r}"
                )

            # ── Step 5: open the execute dialog ──────────────────────────────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                state="visible", timeout=10_000
            )

            # ── Step 6: click Execute → all-delete safety gate fires ──────────
            # All rows are marked 'delete', so the frontend surfaces the
            # EXECUTE_ALL_DELETE_CONFIRM sheet before proceeding.
            execute_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
            execute_btn.wait_for(state="visible", timeout=10_000)
            execute_btn.click()

            # ── Step 7: wait for the all-delete confirmation sheet ────────────
            confirm_sheet = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_sheet.wait_for(state="visible", timeout=10_000)

            # Sanity: the confirmation sheet body should mention a digit (the
            # count of files to be deleted).  This is a soft check — we do not
            # assert the exact wording to avoid coupling to copy changes.
            confirm_text = confirm_sheet.inner_text()
            import re as _re
            assert _re.search(r"[1-9]", confirm_text), (
                f"All-delete confirmation sheet text does not mention a row "
                f"count (expected at least one digit 1-9): {confirm_text!r}"
            )

            # ── Step 8: click Yes to confirm and fire POST /api/execute ───────
            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # ── Step 9: wait for the execute to COMPLETE ──────────────────────
            # The web execute dialog does NOT auto-close on completion — the
            # store replaces manifest.groups from the server response but leaves
            # executeOpen=true (useAppStore.executeDecisions, ~line 414).  The
            # completion signal is therefore the manifest emptying, NOT the
            # dialog hiding.  Poll GET /api/manifest until total_files==0 (every
            # row now has outcome='deleted' and is filtered out by WHERE
            # outcome='' in _LOAD_ALL_SQL).
            deadline = time.monotonic() + 30.0
            post_execute = _get_manifest(base_url, db_path)
            while post_execute.get("total_files", -1) != 0:
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        "Timed out after 30s waiting for total_files==0 after "
                        f"Execute; last total_files={post_execute.get('total_files')} "
                        f"groups={post_execute.get('groups', [])}"
                    )
                time.sleep(0.5)
                post_execute = _get_manifest(base_url, db_path)

            # ── Step 10: ABSENCE-BASED assertions (see docstring) ─────────────
            #
            # (a) total_files==0 confirmed by the poll above — the group vanished
            #     because all rows have outcome='deleted' (do NOT assert
            #     item['outcome']; deleted rows are absent, not visible).
            assert post_execute["total_files"] == 0  # invariant held by the poll

            # (b) Every copied file must be gone from disk.  POST /api/execute
            #     calls send2trash (recycle=true default), which moves files to
            #     the OS recycle bin.  The tmpdir path is the authoritative
            #     check — files are gone from that path.
            still_present = [p for p in copied_paths if os.path.exists(p)]
            assert not still_present, (
                f"POST /api/execute should have deleted all 5 copied files "
                f"but {len(still_present)} are still on disk:\n"
                + "\n".join(f"  {p}" for p in still_present)
            )

    finally:
        # Clean up the tmpdir.  Any files that survive the execute call
        # (e.g. if the test failed before Execute) are removed here so the
        # machine's filesystem stays tidy across repeated runs.
        shutil.rmtree(tmpdir, ignore_errors=True)
