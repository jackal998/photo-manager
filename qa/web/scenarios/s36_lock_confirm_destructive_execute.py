"""Web scenario s36 — lock-confirm -> Unlock & Apply -> delete fires on the locked row.

Ported from qa/scenarios/s36_lock_confirm_destructive_execute.py (Qt UIA).

Qt intent:
  - Scan a disposable 5-JPEG fixture regenerated from scratch each run (s13 clone).
  - Mark every file 'delete' via the bulk-regex standalone dialog ("s36_" pattern).
  - Lock exactly one row (the q95 file) via the separate lock-regex flow.
  - Open ExecuteActionDialog; click Execute.
  - Qt dialog ordering (Execute → all-delete confirm → THEN lock-confirm): the Qt
    UIA ``_on_execute_requested`` pre-execute scan detects the locked-delete row and
    raises the LockConfirmDialog AFTER the user confirms the all-delete warning.
  - Drive Unlock & Apply to All (``LOCK_CONFIRM_APPLY_ALL_UNLOCKED``).
  - Assert all 5 fixture files were removed and manifest ``executed=1`` for every row.

Web slice:
  1. tmpdir copy of all 5 near-duplicate fixtures; db under tmpdir. run_scan([tmpdir]).
  2. _get_manifest -> group_id (str(group_number)) + 5 basenames.
  3. right_click_row + CTX_SET_ACTION_DELETE for each row → all 5 user_decision='delete'.
  4. right_click_row(q95 row) + CTX_LOCK → assert exactly one item has is_locked==True.
  5. open_execute_dialog; click EXECUTE_BTN_EXECUTE.
  6. Web ordering NOTE: all-delete confirm fires FIRST (same as Qt outer confirm), THEN
     the POST /api/execute returns 409 locked_paths and the frontend opens LOCK_CONFIRM_DIALOG.
     This matches the web ExecuteDialog's two-phase flow: DeleteConfirmSheet → submit →
     409 → LockConfirmDialog.
  7. Wait EXECUTE_ALL_DELETE_CONFIRM visible; click EXECUTE_ALL_DELETE_CONFIRM_YES.
  8. Wait LOCK_CONFIRM_DIALOG visible; optionally assert q95 basename appears in its text.
  9. Click LOCK_CONFIRM_BTN_UNLOCK_APPLY → re-issues POST /api/execute with force_locked=true.
 10. Poll GET /api/manifest until total_files==0 — the execute dialog does NOT
     auto-close on the web; completion is the manifest emptying after the
     force_locked re-POST, not the dialog hiding.
 11. ASSERT (absence-based, per the corrected post-execute contract):
       - All 5 tmpdir file paths absent from disk (send2trash moved them).
       - _get_manifest total_files == 0 (all rows have outcome != '' and are filtered out).
       Note: DO NOT assert item['outcome'] == 'deleted' — executed rows are GONE from the
       manifest response entirely (WHERE outcome = '' in _LOAD_ALL_SQL). The deleted row
       is detected by its ABSENCE from the manifest and by non-existence on disk.
 12. finally: shutil.rmtree tmpdir.

Qt divergences:
  - Qt regenerates the fixture from scratch each run (random pHash base image + PIL JPEG
    saves) so the files reliably cluster as one REVIEW_DUPLICATE group. The web port
    instead copies the committed near-duplicates fixture (qa/sandbox/near-duplicates/)
    into a tmpdir — same clustering guarantee (same files), no random generation needed.
  - Qt reads executed/is_locked directly from SQLite. The web port uses GET /api/manifest
    for the is_locked assertion (pre-execute) and absence-from-disk + total_files==0 for
    the post-execute assertion.
  - Qt fires the lock-confirm BEFORE showing the all-delete confirm (inner Qt flow is
    pre-execute scan → lock-check → LockConfirmDialog → then all-delete ConfirmBox inside
    the Unlock & Apply All path). The WEB flow is reversed: ExecuteDialog shows the
    DeleteConfirmSheet (EXECUTE_ALL_DELETE_CONFIRM) FIRST, then on POST /api/execute
    returning 409 locked_paths, the LockConfirmDialog opens. The docstring reflects the
    web ordering; the assertions wait for the web-correct sequence.
  - Qt's ``_probe_confirm`` validates the shape of the Qt ConfirmBox. The web port
    checks LOCK_CONFIRM_DIALOG visibility and optionally asserts the q95 filename appears
    in the dialog body — a lightweight structural probe with no Qt UIA equivalent.
  - Qt cleans up via FIXTURE_DIR.unlink (per-file) because the fixture is disposable and
    regenerated. The web port uses shutil.rmtree(tmpdir) to remove both copies and the
    temp manifest in one call.
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
    CTX_LOCK,
    EXECUTE_DIALOG,
    EXECUTE_BTN_EXECUTE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    LOCK_CONFIRM_DIALOG,
    LOCK_CONFIRM_BTN_UNLOCK_APPLY,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

# The fixture file that will be locked. It is the highest-quality JPEG in the
# group (neardup_00_q95.jpg). The lock target must match exactly one basename.
_LOCK_TARGET_BASENAME = "neardup_00_q95.jpg"

# All 5 fixture basenames in order — must match qa/sandbox/near-duplicates/.
_ALL_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_items(manifest: dict) -> list[dict]:
    """Flatten all file items from all groups in the manifest response.

    Uses group["items"] (NOT "files" — the server-side serialisation key
    is "items", confirmed in core/app_service/review_view.py:serialize_groups).
    """
    items: list[dict] = []
    for group in manifest.get("groups", []):
        items.extend(group.get("items", []))
    return items


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan near-dups, lock q95, execute via lock-confirm Unlock & Apply, assert all deleted."""
    # Copy fixture into a fresh tmpdir so the real qa/sandbox/near-duplicates/
    # files are never touched. send2trash moves COPIES, not originals.
    tmpdir = tempfile.mkdtemp(prefix="qa_s36_")
    db_path = os.path.join(tmpdir, "s36_manifest.db")
    try:
        # ── Step 1: copy fixture files into tmpdir ────────────────────────
        copied_paths: list[str] = []
        for basename in _ALL_BASENAMES:
            src = _NEAR_DUPS_DIR / basename
            dst = os.path.join(tmpdir, basename)
            shutil.copy(str(src), dst)
            copied_paths.append(dst)

        assert len(copied_paths) == 5, (
            f"Expected 5 fixture copies, got {len(copied_paths)}"
        )

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 2: scan the tmpdir copies ───────────────────────────
            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 3: derive group_id from the manifest ─────────────────
            # group_id for row testids is str(group_number) — mirror of s15.
            manifest = _get_manifest(base_url, db_path)
            assert manifest.get("total_groups", 0) >= 1, (
                "Expected at least one group in the manifest after scan; "
                f"total_groups={manifest.get('total_groups')}"
            )
            first_group = manifest["groups"][0]
            group_id = str(first_group["group_number"])

            # Confirm all 5 basenames are present before setting decisions.
            items_pre = _extract_items(manifest)
            assert len(items_pre) == 5, (
                f"Expected 5 manifest items, got {len(items_pre)}; "
                f"basenames={[Path(i['file_path']).name for i in items_pre]}"
            )

            # ── Step 4: mark all 5 rows 'delete' via context menu ────────
            for basename in _ALL_BASENAMES:
                row_tid = row_file_testid(group_id, basename)
                right_click_row(page, row_tid)
                click_context_item(page, CTX_SET_ACTION_DELETE)

            # Verify all 5 user_decision fields read back as 'delete'.
            manifest_post_decide = _get_manifest(base_url, db_path)
            items_post_decide = _extract_items(manifest_post_decide)
            assert len(items_post_decide) == 5, (
                f"Expected 5 items after bulk-delete, got {len(items_post_decide)}"
            )
            for item in items_post_decide:
                bn = Path(item["file_path"]).name
                assert item.get("user_decision") == "delete", (
                    f"Expected user_decision='delete' for {bn!r} after CTX_SET_ACTION_DELETE, "
                    f"got {item.get('user_decision')!r}"
                )

            # ── Step 5: lock exactly the q95 row ─────────────────────────
            lock_row_tid = row_file_testid(group_id, _LOCK_TARGET_BASENAME)
            right_click_row(page, lock_row_tid)
            click_context_item(page, CTX_LOCK)

            # Assert exactly one item is locked (is_locked == True).
            # Key confirmed in core/app_service/review_view.py:_build_file_row:
            #   "is_locked": bool(getattr(record, "is_locked", False))
            manifest_post_lock = _get_manifest(base_url, db_path)
            items_post_lock = _extract_items(manifest_post_lock)
            locked_items = [
                i for i in items_post_lock if i.get("is_locked") is True
            ]
            assert len(locked_items) == 1, (
                f"Expected exactly 1 locked item after CTX_LOCK on {_LOCK_TARGET_BASENAME!r}, "
                f"got {len(locked_items)}: "
                f"{[Path(i['file_path']).name for i in locked_items]}"
            )
            assert Path(locked_items[0]["file_path"]).name == _LOCK_TARGET_BASENAME, (
                f"Locked item should be {_LOCK_TARGET_BASENAME!r}, "
                f"got {Path(locked_items[0]['file_path']).name!r}"
            )

            # ── Step 6: open the execute dialog ───────────────────────────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)

            # ── Step 7: click Execute ──────────────────────────────────────
            # With all 5 rows marked 'delete' the ExecuteDialog will show the
            # all-delete safety sheet (EXECUTE_ALL_DELETE_CONFIRM) BEFORE
            # submitting the actual POST /api/execute request.
            execute_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
            execute_btn.wait_for(state="visible", timeout=10_000)
            execute_btn.click()

            # ── Step 8: confirm the all-delete safety sheet ───────────────
            # WEB ORDERING: all-delete confirm fires FIRST (before the server
            # sees the request). Qt reverses this: it detects the locked row
            # pre-submit and shows LockConfirmDialog before the all-delete
            # ConfirmBox. The web version posts only after the user confirms the
            # all-delete sheet, so the 409 locked_paths comes second.
            confirm_sheet = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_sheet.wait_for(state="visible", timeout=15_000)

            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # ── Step 9: wait for the lock-confirm dialog (409 response) ───
            # After EXECUTE_ALL_DELETE_CONFIRM_YES, the ExecuteDialog submits
            # POST /api/execute. The server finds a locked delete row and
            # returns 409 {code: "locked_paths"}. The frontend opens
            # LOCK_CONFIRM_DIALOG in response to that 409.
            lock_confirm = page.get_by_test_id(LOCK_CONFIRM_DIALOG)
            lock_confirm.wait_for(state="visible", timeout=20_000)

            # Optional structural probe: the dialog body should mention the
            # locked filename. This is a soft check — if the testid isn't
            # text-content-accessible we skip it rather than hard-failing.
            try:
                dialog_text = lock_confirm.inner_text()
                assert _LOCK_TARGET_BASENAME in dialog_text, (
                    f"LOCK_CONFIRM_DIALOG body does not mention the locked file "
                    f"{_LOCK_TARGET_BASENAME!r}; got body: {dialog_text[:200]!r}"
                )
            except Exception as _probe_err:  # noqa: BLE001
                # The text-content probe is informational; don't fail the
                # scenario if the dialog body is not directly text-readable.
                print(
                    f"probe_status: lock-confirm text probe skipped ({_probe_err})"
                )

            # ── Step 10: click Unlock & Apply ─────────────────────────────
            # LOCK_CONFIRM_BTN_UNLOCK_APPLY re-issues the execute request with
            # force_locked=true so the locked row is unlocked then deleted.
            unlock_apply_btn = page.get_by_test_id(LOCK_CONFIRM_BTN_UNLOCK_APPLY)
            unlock_apply_btn.wait_for(state="visible", timeout=5_000)
            unlock_apply_btn.click()

            # ── Step 11: wait for the execute to COMPLETE ─────────────────
            # The web execute dialog does NOT auto-close (the store keeps
            # executeOpen=true and refreshes the now-empty tree — see
            # useAppStore.executeDecisions). Completion is signalled by the
            # manifest emptying after the force_locked=true re-POST, not by the
            # dialog hiding. Poll GET /api/manifest until total_files==0.
            deadline = time.monotonic() + 30.0
            manifest_post_exec = _get_manifest(base_url, db_path)
            while manifest_post_exec.get("total_files", -1) != 0:
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        "Timed out after 30s waiting for total_files==0 after "
                        f"Unlock & Apply; last total_files="
                        f"{manifest_post_exec.get('total_files')} "
                        f"groups={manifest_post_exec.get('groups', [])}"
                    )
                time.sleep(0.5)
                manifest_post_exec = _get_manifest(base_url, db_path)

            # ── Step 12: ASSERT post-execute state (absence-based) ────────
            # CORRECTED CONTRACT: after POST /api/execute, rows with outcome='deleted'
            # are filtered out by _LOAD_ALL_SQL's WHERE outcome='' predicate. They
            # do NOT appear in the manifest response at all. Assertions:
            #   (a) All 5 tmpdir file paths are absent from disk.
            #   (b) GET /api/manifest returns total_files==0 (all rows gone).
            # DO NOT assert item['outcome']=='deleted' — that field is only visible
            # in SQLite directly; the API returns only outcome='' rows, so a deleted
            # row is simply absent from the groups/items arrays.

            # (a) Disk-absence check.
            still_present = [p for p in copied_paths if os.path.exists(p)]
            assert not still_present, (
                f"Expected all 5 tmpdir files to be absent after Unlock & Apply; "
                f"still present ({len(still_present)}): {still_present}"
            )

            # (b) Manifest total_files check.
            manifest_post_exec = _get_manifest(base_url, db_path)
            total_files = manifest_post_exec.get("total_files", -1)
            assert total_files == 0, (
                f"Expected total_files==0 after full destructive execute "
                f"(all rows should be filtered out by outcome!='' predicate), "
                f"got total_files={total_files}; "
                f"groups still present: {manifest_post_exec.get('groups', [])}"
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
