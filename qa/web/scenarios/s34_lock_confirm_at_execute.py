"""Web scenario s34 — execute-time lock-confirm, Cancel verdict (non-destructive).

Ported from qa/scenarios/s34_lock_confirm_at_execute.py (Qt UIA, #182).

Qt intent:
  - Scan the 5-JPEG near-duplicates fixture; mark every row 'delete'; lock the
    q95 row AFTER its decision is set (locking is free, no confirm).
  - Open ExecuteActionDialog; click Execute. The pre-execute scan finds q95
    (locked + decision='delete') and raises the unified LockedRowsConfirmDialog
    BEFORE any destructive action runs.
  - Drive the **Cancel** verdict: the dialog dismisses, _on_execute_requested
    returns WITHOUT executing, and the manifest is left exactly as it was.
  - Assert (a) the manifest is unchanged vs the pre-execute snapshot and (b) the
    Execute Action dialog is STILL on screen (Cancel aborted the destructive op
    without dismissing the review dialog).
  - The other two verdicts (Unlock & Apply All, Apply to Unlocked Only) are
    pinned elsewhere — Unlock & Apply by the web s36, Unlocked-Only by s32 —
    so s34 exclusively exercises Cancel.

Web slice:
  1. tmpdir copy of all 5 near-duplicate fixtures; db under tmpdir. run_scan([tmpdir]).
  2. _get_manifest -> group_id (str(group_number)) + 5 basenames.
  3. right_click_row + CTX_SET_ACTION_DELETE for each row → all 5 user_decision='delete'.
  4. right_click_row(q95 row) + CTX_LOCK → assert exactly one item has is_locked==True.
  5. Snapshot the PRE-EXECUTE state: {basename: (user_decision, is_locked)} for all 5.
  6. open_execute_dialog; click EXECUTE_BTN_EXECUTE.
  7. Web ordering NOTE (same as s36): all-delete confirm fires FIRST
     (EXECUTE_ALL_DELETE_CONFIRM → _YES) → POST /api/execute returns 409
     locked_paths → the frontend opens LOCK_CONFIRM_DIALOG. The 409 is a
     pre-flight rejection: nothing has been deleted at this point.
  8. Assert the lock-confirm body makes the delete-now consequence explicit —
     it must contain "DELETE" (web body copy: "...permanently DELETED..."). This
     is the IMMEDIATE/execute context, distinct from the DEFERRED remove context.
  9. Click LOCK_CONFIRM_BTN_CANCEL → handleCancel() for op=="execute" clears
     lockConflict (LockConfirmDialog.tsx:108). The lock-confirm dialog dismisses.
 10. ASSERT (non-destructive contract):
       - LOCK_CONFIRM_DIALOG is now hidden (lockConflict cleared).
       - EXECUTE_DIALOG stays VISIBLE — #721 Qt parity: cancelling the nested
         lock-confirm must NOT close the Execute dialog. The child modal's
         focus-return fires a `focusin` that ExecuteDialog's DismissableLayer
         sees as an interact-outside; ExecuteDialog now preventDefaults
         focus-origin interactions so the parent stays open, matching Qt (the
         user can adjust and retry). The safety contract is unchanged.
       - GET /api/manifest equals the pre-execute snapshot: all 5 rows still
         user_decision='delete', q95 still is_locked=True, total_files unchanged.
       - All 5 tmpdir files still present on disk (the 409 + Cancel deleted nothing).
 11. finally: shutil.rmtree tmpdir.

Qt divergences:
  - Qt fires the lock-confirm BEFORE the all-delete confirm (its pre-execute scan
    detects the locked row first). The WEB reverses this: the DeleteConfirmSheet
    (all-delete) shows first, then the POST 409 opens LockConfirmDialog. Final
    Cancel outcome is identical: nothing executed.
  - Qt asserts the IMMEDIATE apply-button LABEL
    (LOCK_CONFIRM_BTN_UNLOCK_DELETE_IMMEDIATE). The web has NO per-context button
    variant — the destructive button is always "Unlock & Apply" — so the
    IMMEDIATE/delete-now cue is asserted on the dialog BODY text ("DELETED")
    instead of a button label.
  - Qt reads user_decision/is_locked directly from SQLite (_read_state). The web
    port snapshots + re-reads via GET /api/manifest (the serialised authority),
    plus a disk-absence guard that nothing was deleted.
  - Qt regenerates the fixture per run; the web port copies the committed
    qa/sandbox/near-duplicates/ files into a tmpdir (same clustering guarantee,
    no random generation), and cleans up via shutil.rmtree.
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
    CTX_LOCK,
    EXECUTE_DIALOG,
    EXECUTE_BTN_EXECUTE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    LOCK_CONFIRM_DIALOG,
    LOCK_CONFIRM_BTN_CANCEL,
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


def _state_map(manifest: dict) -> dict[str, tuple[str, bool]]:
    """Return {basename: (user_decision, is_locked)} for every manifest row.

    This is the web analog of the Qt scenario's _read_state — the comparable
    snapshot used to prove the manifest is unchanged after the Cancel verdict.
    """
    state: dict[str, tuple[str, bool]] = {}
    for item in _extract_items(manifest):
        basename = Path(item["file_path"]).name
        state[basename] = (
            item.get("user_decision") or "",
            item.get("is_locked") is True,
        )
    return state


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Lock q95, Execute → lock-confirm, drive Cancel, assert nothing executed."""
    # Copy fixture into a fresh tmpdir so the real qa/sandbox/near-duplicates/
    # files are never touched. The Cancel verdict deletes nothing, but the
    # tmpdir keeps the scenario hermetic regardless.
    tmpdir = tempfile.mkdtemp(prefix="qa_s34_")
    db_path = os.path.join(tmpdir, "s34_manifest.db")
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
            manifest = _get_manifest(base_url, db_path)
            assert manifest.get("total_groups", 0) >= 1, (
                "Expected at least one group in the manifest after scan; "
                f"total_groups={manifest.get('total_groups')}"
            )
            first_group = manifest["groups"][0]
            group_id = str(first_group["group_number"])

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

            manifest_post_decide = _get_manifest(base_url, db_path)
            items_post_decide = _extract_items(manifest_post_decide)
            assert len(items_post_decide) == 5, (
                f"Expected 5 items after bulk-delete, got {len(items_post_decide)}"
            )
            for item in items_post_decide:
                bn = Path(item["file_path"]).name
                assert item.get("user_decision") == "delete", (
                    f"Expected user_decision='delete' for {bn!r} after "
                    f"CTX_SET_ACTION_DELETE, got {item.get('user_decision')!r}"
                )

            # ── Step 5: lock exactly the q95 row ─────────────────────────
            lock_row_tid = row_file_testid(group_id, _LOCK_TARGET_BASENAME)
            right_click_row(page, lock_row_tid)
            click_context_item(page, CTX_LOCK)

            manifest_post_lock = _get_manifest(base_url, db_path)
            items_post_lock = _extract_items(manifest_post_lock)
            locked_items = [
                i for i in items_post_lock if i.get("is_locked") is True
            ]
            assert len(locked_items) == 1, (
                f"Expected exactly 1 locked item after CTX_LOCK on "
                f"{_LOCK_TARGET_BASENAME!r}, got {len(locked_items)}: "
                f"{[Path(i['file_path']).name for i in locked_items]}"
            )
            assert Path(locked_items[0]["file_path"]).name == _LOCK_TARGET_BASENAME, (
                f"Locked item should be {_LOCK_TARGET_BASENAME!r}, "
                f"got {Path(locked_items[0]['file_path']).name!r}"
            )

            # ── Step 6: snapshot the PRE-EXECUTE state ────────────────────
            # All 5 'delete'; q95 locked. This is the authority the post-Cancel
            # manifest must match exactly (the non-destructive contract).
            pre_execute = _state_map(manifest_post_lock)
            assert pre_execute[_LOCK_TARGET_BASENAME] == ("delete", True), (
                f"Pre-execute snapshot wrong for {_LOCK_TARGET_BASENAME!r}: "
                f"{pre_execute[_LOCK_TARGET_BASENAME]} (expected ('delete', True))"
            )

            # ── Step 7: open the execute dialog and click Execute ─────────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)

            execute_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
            execute_btn.wait_for(state="visible", timeout=10_000)
            execute_btn.click()

            # ── Step 8: confirm the all-delete safety sheet (web ordering) ─
            # With all 5 rows marked 'delete' the all-delete confirm fires
            # before the POST. Qt reverses this ordering; the final Cancel
            # outcome is identical.
            confirm_sheet = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_sheet.wait_for(state="visible", timeout=15_000)
            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # ── Step 9: lock-confirm dialog opens from the 409 response ───
            lock_confirm = page.get_by_test_id(LOCK_CONFIRM_DIALOG)
            lock_confirm.wait_for(state="visible", timeout=20_000)

            # IMMEDIATE/execute context: the body must make the delete-now
            # consequence explicit. Web body copy reads "...permanently
            # DELETED..." — assert the DELETE cue (replaces Qt's per-context
            # apply-button-label assertion, which the web has no equivalent of).
            dialog_text = lock_confirm.inner_text()
            assert "DELETE" in dialog_text.upper(), (
                f"LOCK_CONFIRM_DIALOG body must make the delete-now consequence "
                f"explicit (expected 'DELETE' in body); got: {dialog_text[:200]!r}"
            )

            # ── Step 10: drive the Cancel verdict ─────────────────────────
            cancel_btn = page.get_by_test_id(LOCK_CONFIRM_BTN_CANCEL)
            cancel_btn.wait_for(state="visible", timeout=5_000)
            cancel_btn.click()

            # The lock-confirm dialog dismisses (handleCancel clears lockConflict).
            lock_confirm.wait_for(state="hidden", timeout=10_000)

            # #721 — Qt parity: cancelling the lock-confirm must keep the Execute
            # review dialog OPEN so the user can adjust and retry. The lock-confirm
            # is a Radix modal portaled as a SIBLING of ExecuteDialog, so when it
            # unmounts Radix returns focus and fires a `focusin` that
            # ExecuteDialog's DismissableLayer sees as an interact-outside (the
            # pointerdown path is stack-gated while the child modal is open, so
            # only the focus path leaked). ExecuteDialog now preventDefaults
            # focus-origin interactions (ExecuteDialog.tsx onInteractOutside), so
            # the parent stays open. This is a REAL-BROWSER behaviour that jsdom
            # cannot exercise (it does not fire the focus-return focusin on child
            # unmount), which is exactly why it is asserted here in a live
            # scenario rather than a vitest unit test.
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)

            # ── Step 11: ASSERT the manifest is unchanged (nothing executed) ─
            manifest_post_cancel = _get_manifest(base_url, db_path)
            post_cancel = _state_map(manifest_post_cancel)
            assert post_cancel == pre_execute, (
                "Manifest changed after the Cancel verdict (expected no-op). "
                f"pre={pre_execute} post={post_cancel}"
            )
            assert manifest_post_cancel.get("total_files") == 5, (
                "Expected total_files==5 after Cancel (nothing deleted); got "
                f"{manifest_post_cancel.get('total_files')}"
            )

            # Disk guard: the 409 pre-flight + Cancel deleted nothing.
            missing = [p for p in copied_paths if not os.path.exists(p)]
            assert not missing, (
                f"Expected all 5 tmpdir files to remain on disk after Cancel; "
                f"missing ({len(missing)}): {missing}"
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
