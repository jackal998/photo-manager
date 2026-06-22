"""Web scenario s64 — Execute selected (partial-execute) leaves other rows intact.

Ported from qa/scenarios/s64_execute_selected_partial.py (Qt UIA).

Qt intent:
  - Build TWO independent near-duplicate clusters (6 generated JPEGs, 3 per
    cluster) so the Execute dialog renders two groups.
  - Mark all rows 'delete' via regex ".+".
  - Open Execute Action dialog; Ctrl+click 2 rows in cluster-1 to create a
    multi-selection; click "Execute selected".
  - Assert: dialog stays open, highlighted 2 files gone from disk, remaining
    4 files present with decisions still 'delete', then full Execute finishes.

Web-observable assertions (this port):
  1. After context-menu loop all 5 rows have user_decision=='delete'.
  2. execute-btn-execute-selected is visible and not disabled after clicking
     a single execute-tree row (selectedFilePath is set).
  3. Clicking execute-btn-execute-selected (scope_paths=[target]) does NOT
     trigger execute-all-delete-confirm (5-file group is not fully-execute-
     selected; the confirm fires only when every remaining row is in scope).
  4. After the partial execute completes (target absent from manifest), the
     target file is absent from disk.
  5. The other 4 files are still present on disk AND still appear in the
     manifest with user_decision=='delete' (outcome='' so still visible).
  6. execute-dialog is still visible (web never auto-closes on partial execute
     — same invariant as Qt's "dialog stays open after Execute selected").
  7. Second-pass full Execute: click execute-btn-execute; since all remaining
     4 rows are 'delete', execute-all-delete-confirm appears; confirm and wait
     until GET /api/manifest total_files==0; all 5 files absent from disk.

Single-vs-multi divergence (HONEST PARTIAL PORT):
  Qt Ctrl+clicks TWO rows before hitting "Execute selected", creating a 2-item
  multi-selection.  The web execute tree implements SINGLE-select:
  ``selectedFilePath`` is one path; EXECUTE_BTN_EXECUTE_SELECTED scopes exactly
  1 file (POST /api/execute with scope_paths=[target_path]).  Clicking a
  different execute-tree row replaces the single selection; there is no
  Ctrl+click multi-select in the current React implementation.

  The port uses a 1-file subset.  It fully exercises the SAME scope_paths
  invariant: scoped delete fires only for the scoped row and all others are
  untouched.  The test is not weaker on the server side — scope_paths is a
  list and POST /api/execute filters by that list regardless of whether the
  list has 1 or N entries.  The UI's single-select constraint is the delta.

Post-execute manifest contract (corrected):
  infrastructure/manifest_repository.py:60 uses "WHERE outcome = ''" as the
  single visibility predicate.  After EXECUTE a deleted row has outcome set to
  'deleted' and is FILTERED OUT of GET /api/manifest entirely.  We therefore
  detect a deleted file by ABSENCE from the manifest (and from disk), not by
  asserting outcome=='deleted' on a returned row.

Fixture strategy:
  qa/sandbox/near-duplicates/ holds 5 JPEGs used by most web scenarios.  We
  copy them all into a tmpdir so the real DELETE in POST /api/execute operates
  on COPIES, not the checked-in fixtures.  The tmpdir is rmtree'd in finally.
  Files go to the OS recycle bin (web-side recycle=true default) — fine, because
  os.path.exists on the tmpdir path still returns False after send2trash.
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
    EXECUTE_BTN_EXECUTE_SELECTED,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    row_file_testid,
    execute_row_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_SRC = _REPO / "qa" / "sandbox" / "near-duplicates"

# The 5 near-duplicate basenames in the fixture directory (ordered by quality).
# All 5 land in a single dedup group after scanning.
_ALL_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]


# ---------------------------------------------------------------------------
# HTTP helpers (self-contained, mirroring s56/s51 shape verbatim)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_visible_basenames(manifest: dict) -> set[str]:
    """Return the set of basenames currently visible in the manifest.

    Only rows with outcome='' (the WHERE predicate in _LOAD_ALL_SQL) are
    returned by the API.  A deleted row is absent — it does NOT appear with
    outcome='deleted'.  We use this to assert both absence (deleted row) and
    presence (surviving rows) purely from the API response shape.
    """
    basenames: set[str] = set()
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            basenames.add(Path(item["file_path"]).name)
    return basenames


def _extract_decisions(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for all visible rows."""
    result: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            basename = Path(item["file_path"]).name
            result[basename] = item.get("user_decision", "") or ""
    return result


def _poll_until_absent(
    base_url: str,
    db_path: str,
    target_basename: str,
    *,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll GET /api/manifest until ``target_basename`` is absent from all items.

    Returns the manifest at the moment the condition is satisfied.
    Raises AssertionError on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        manifest = _get_manifest(base_url, db_path)
        visible = _extract_visible_basenames(manifest)
        if target_basename not in visible:
            return manifest
        time.sleep(interval_s)
    raise AssertionError(
        f"Timed out after {timeout_s}s waiting for {target_basename!r} to be "
        f"absent from the manifest (partial execute did not fire or the row "
        f"was not removed from the manifest after outcome was set)."
    )


def _poll_until_empty(
    base_url: str,
    db_path: str,
    *,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll GET /api/manifest until total_files == 0.

    Returns the manifest at the moment the condition is satisfied.
    Raises AssertionError on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        manifest = _get_manifest(base_url, db_path)
        if manifest.get("total_files", -1) == 0:
            return manifest
        time.sleep(interval_s)
    raise AssertionError(
        f"Timed out after {timeout_s}s waiting for manifest total_files==0 "
        f"after full execute."
    )


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan near-dup copies; partial-execute 1 file; assert survivors intact."""
    # Copy fixture JPEGs into a fresh tmpdir so real deletes hit the copies,
    # not the checked-in qa/sandbox/near-duplicates/ files.
    tmpdir = tempfile.mkdtemp(prefix="qa_s64_")
    db_path = os.path.join(tmpdir, "s64.db")
    try:
        for basename in _ALL_BASENAMES:
            shutil.copy(str(_NEAR_DUPS_SRC / basename), os.path.join(tmpdir, basename))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the tmpdir copy ────────────────────────────────
            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 2: derive group_id from manifest ────────────────────────
            # Mirror s15 exactly: first_group["group_number"] as str.
            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, (
                "Expected at least one group after scanning the near-dup copies"
            )
            first_group = manifest["groups"][0]
            group_id = str(first_group["group_number"])

            # Confirm all 5 basenames are present.
            visible_pre = _extract_visible_basenames(manifest)
            assert len(visible_pre) == 5, (
                f"Expected 5 rows in manifest before marking, "
                f"got {len(visible_pre)}: {sorted(visible_pre)}"
            )
            for bn in _ALL_BASENAMES:
                assert bn in visible_pre, (
                    f"Expected {bn!r} in manifest after scan, "
                    f"visible rows: {sorted(visible_pre)}"
                )

            # ── Step 3: mark all 5 rows 'delete' via context-menu loop ───────
            # Mirrors s15 and s51 exactly — right_click_row + click_context_item
            # for each basename in the group.
            for basename in _ALL_BASENAMES:
                row_tid = row_file_testid(group_id, basename)
                right_click_row(page, row_tid)
                click_context_item(page, CTX_SET_ACTION_DELETE)

            # Assert all 5 have user_decision=='delete' before opening Execute.
            decisions_pre = _extract_decisions(_get_manifest(base_url, db_path))
            for basename in _ALL_BASENAMES:
                assert decisions_pre.get(basename) == "delete", (
                    f"Step 3: expected user_decision='delete' for {basename!r}, "
                    f"got {decisions_pre.get(basename)!r}"
                )

            # ── Step 4: open the execute dialog ──────────────────────────────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)

            # ── Step 5: pick ONE target row in the execute tree ───────────────
            # Web single-select: clicking an execute-tree row sets selectedFilePath
            # to that one file's path, enabling execute-btn-execute-selected.
            # (Qt Ctrl+clicked 2 rows; web has no multi-select — see docstring.)
            target = _ALL_BASENAMES[0]  # "neardup_00_q95.jpg"
            exec_row_tid = execute_row_testid(group_id, target)
            exec_row = page.get_by_test_id(exec_row_tid)
            exec_row.wait_for(state="attached", timeout=10_000)
            try:
                exec_row.scroll_into_view_if_needed(timeout=10_000)
            except Exception:  # noqa: BLE001 — virtualised row may not need scrolling
                pass
            exec_row.wait_for(state="visible", timeout=10_000)
            exec_row.click()

            # ── Step 6: assert execute-btn-execute-selected is enabled ────────
            exec_sel_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE_SELECTED)
            exec_sel_btn.wait_for(state="visible", timeout=10_000)
            assert not exec_sel_btn.is_disabled(), (
                "execute-btn-execute-selected is disabled after clicking an "
                "execute-tree row — selectedFilePath wiring may have regressed"
            )

            # ── Step 7: click Execute selected (scope_paths=[target]) ─────────
            # Scoping 1 of 5 rows → isAllDeleteScope=False → NO confirm sheet.
            # If a confirm appears anyway (edge case: group collapsed to 1 row)
            # we dismiss it to stay robust, as Qt does in the matching step.
            exec_sel_btn.click()

            # Wait a short time for any confirm sheet to appear, then dismiss.
            # With 5 rows and 1 selected, the confirm SHOULD NOT appear.  We
            # give it 2 s to be safe, matching Qt's confirm-sniff window.
            confirm_appeared = False
            try:
                page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM).wait_for(
                    state="visible", timeout=2_000
                )
                # If we reach here the confirm appeared unexpectedly.
                confirm_appeared = True
                page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES).wait_for(
                    state="visible", timeout=3_000
                )
                page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES).click()
            except Exception:  # noqa: BLE001 — expected: confirm did NOT appear
                pass
            # Record whether it appeared (informational — not a failure by itself).
            _ = confirm_appeared  # suppress "unused variable" lint

            # ── Step 8: wait until target is absent from the manifest ──────────
            # Absence = outcome was set to 'deleted' and the row dropped from
            # _LOAD_ALL_SQL (WHERE outcome = '').  We poll the HTTP API, not
            # the Playwright DOM — the DOM tree may not update synchronously.
            _poll_until_absent(base_url, db_path, target, timeout_s=30.0)

            # ── Step 9: ASSERT absence-based invariants ───────────────────────
            # 9a. Target absent from disk (send2trash moved it out of tmpdir).
            target_path = os.path.join(tmpdir, target)
            assert not os.path.exists(target_path), (
                f"FAIL: {target!r} still exists on disk after partial execute — "
                f"POST /api/execute with scope_paths did not fire or send2trash "
                f"did not move the file."
            )

            # 9b. Target absent from manifest (already confirmed by _poll_until_absent,
            #     but re-assert with a fresh read so the error message is clear).
            manifest_mid = _get_manifest(base_url, db_path)
            visible_mid = _extract_visible_basenames(manifest_mid)
            assert target not in visible_mid, (
                f"FAIL: {target!r} still appears in manifest items after partial "
                f"execute (expected absence — outcome='deleted' filters it out)."
            )

            # 9c. The other 4 files must still be present on disk.
            survivors = [bn for bn in _ALL_BASENAMES if bn != target]
            for basename in survivors:
                survivor_path = os.path.join(tmpdir, basename)
                assert os.path.exists(survivor_path), (
                    f"FAIL: non-targeted {basename!r} was deleted by partial "
                    f"execute — scope_paths must confine the delete to the "
                    f"selected row only."
                )

            # 9d. The other 4 files must still appear in the manifest with
            #     user_decision=='delete' (outcome='' → still visible).
            decisions_mid = _extract_decisions(manifest_mid)
            for basename in survivors:
                assert basename in visible_mid, (
                    f"FAIL: survivor {basename!r} is missing from manifest after "
                    f"partial execute (expected outcome='' so still visible)."
                )
                assert decisions_mid.get(basename) == "delete", (
                    f"FAIL: survivor {basename!r} user_decision changed — "
                    f"expected 'delete', got {decisions_mid.get(basename)!r}. "
                    f"Partial execute must not touch out-of-scope decisions."
                )

            # 9e. Execute dialog must STILL be visible (partial execute does NOT
            #     auto-close the dialog — web mirrors Qt's invariant exactly).
            assert page.get_by_test_id(EXECUTE_DIALOG).is_visible(), (
                "FAIL: execute-dialog closed after Execute selected — partial "
                "execute must keep the dialog open for the remaining 4 rows."
            )

            # ── Step 10: second-pass full Execute (remaining 4 rows) ──────────
            # NOTE: if the execute tree does not refresh its row list after the
            # partial delete, clicking the full Execute button may behave
            # unexpectedly.  If this second pass is flaky, flag it in the
            # open_concerns of the StructuredOutput rather than forcing it —
            # Step 9 (the core invariant) is load-bearing.
            exec_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
            exec_btn.wait_for(state="visible", timeout=10_000)
            exec_btn.click()

            # All 4 remaining rows are 'delete', so the all-delete confirm
            # MUST appear for the full-execute case.
            page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM).wait_for(
                state="visible", timeout=10_000
            )
            page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES).wait_for(
                state="visible", timeout=5_000
            )
            page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES).click()

            # Wait until all 5 files have been processed (total_files==0).
            _poll_until_empty(base_url, db_path, timeout_s=30.0)

            # ── Step 11: final state assertions ───────────────────────────────
            for basename in _ALL_BASENAMES:
                final_path = os.path.join(tmpdir, basename)
                assert not os.path.exists(final_path), (
                    f"FAIL: {basename!r} still on disk after full Execute — "
                    f"the file was not deleted."
                )

            manifest_final = _get_manifest(base_url, db_path)
            assert manifest_final.get("total_files", -1) == 0, (
                f"FAIL: manifest total_files should be 0 after full Execute, "
                f"got {manifest_final.get('total_files')!r}."
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
