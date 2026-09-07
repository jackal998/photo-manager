"""Web scenario s51 — Execute dialog preview pane is visible (#165).

Ported from qa/scenarios/s51_execute_dialog_preview.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files in one group).
  - Mark all rows 'delete' so the Execute button enables and the dialog
    has rows the user can click.
  - Open ExecuteActionDialog.
  - Assert the QSplitter + PreviewPane header are present in the UIA tree
    (proves preview-enabled layout wired up; task_runner is not None).
  - Click a file row; confirm the app does not crash (UIA still responsive).
  - Close without clicking Execute.
  - Confirm manifest decisions are unchanged (Close is non-destructive).

Web-observable assertions:
  1. After opening the execute dialog, ``execute-preview-image`` becomes
     visible when a row in the execute tree is clicked.
  2. The execute dialog is still open (execute-dialog is visible) and
     the preview pane (execute-preview-pane) is mounted in the DOM.
  3. Manifest decisions are unchanged after Close (non-destructive).

Qt divergences:
  - Qt locates the QSplitter by UIA class_name='QSplitter' — no DOM
    equivalent.  The web port asserts ``execute-preview-pane`` visibility,
    which is the structural equivalent (the pane only mounts when the
    preview-enabled layout fires).
  - Qt asserts the 'Preview' header label text — the web preview pane
    testid is ``execute-preview-pane``; the heading text is not independently
    testid'd. We assert pane visibility instead.
  - Qt clicks the first TreeItem descendant to drive show_single and confirms
    no crash via a second UIA locate.  The web port right-clicks the first
    execute-tree row to trigger selection (the click itself is the selection
    trigger), then asserts ``execute-preview-image`` becomes visible.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.request
import urllib.parse
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
    EXECUTE_PREVIEW_PANE,
    EXECUTE_PREVIEW_IMAGE,
    row_file_testid,
    execute_row_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    """Scan near-dups, mark all delete, open execute dialog, assert preview visible."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Scan the near-duplicates fixture.
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # Derive the group_id from the manifest to build row testids.
            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, (
                "Expected at least one group in the near-duplicates manifest"
            )
            first_group = manifest["groups"][0]
            group_id = str(first_group["group_number"])

            # Mark each file row 'delete' via the context menu so the
            # Execute button enables (has-decisions gate).
            basenames = [
                Path(f["file_path"]).name
                for f in first_group.get("items", [])
            ]
            assert basenames, "No file rows found in first group"

            for basename in basenames:
                row_tid = row_file_testid(group_id, basename)
                right_click_row(page, row_tid)
                click_context_item(page, CTX_SET_ACTION_DELETE)

            # Confirm decisions are written before opening execute dialog.
            post_decision = _get_manifest(base_url, db_path)
            first_group_post = post_decision["groups"][0]
            for f in first_group_post.get("items", []):
                assert f.get("user_decision") == "delete", (
                    f"Expected user_decision='delete' for {f['file_path']!r}, "
                    f"got {f.get('user_decision')!r}"
                )

            # Open the execute dialog (non-destructive; do NOT click Execute).
            open_execute_dialog(page)

            # The execute dialog must be visible.
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)

            # The execute preview pane must be mounted in the DOM.
            pane = page.get_by_test_id(EXECUTE_PREVIEW_PANE)
            pane.wait_for(state="visible", timeout=10_000)

            # Click the first file row in the execute tree to trigger
            # selection → preview image loads.
            # The execute tree is virtualised (@tanstack/react-virtual with
            # position:absolute rows).  scroll_into_view_if_needed() forces
            # the virtualiser to render the row before we assert visibility.
            first_basename = basenames[0]
            exec_row_tid = execute_row_testid(group_id, first_basename)
            exec_row = page.get_by_test_id(exec_row_tid)
            exec_row.wait_for(state="attached", timeout=10_000)
            try:
                exec_row.scroll_into_view_if_needed(timeout=10_000)
            except Exception:  # noqa: BLE001 — virtualised row may not need scrolling
                pass
            exec_row.wait_for(state="visible", timeout=10_000)
            exec_row.click()

            # The preview image must become visible after row selection.
            preview_img = page.get_by_test_id(EXECUTE_PREVIEW_IMAGE)
            preview_img.wait_for(state="visible", timeout=15_000)

            # Close the dialog WITHOUT clicking Execute (non-destructive check).
            # Press Escape to dismiss (avoids needing a separate close-button testid).
            page.keyboard.press("Escape")
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="hidden", timeout=5_000)

            # Manifest decisions must be unchanged after Close (Close != Execute).
            post_close = _get_manifest(base_url, db_path)
            first_group_close = post_close["groups"][0]
            for f in first_group_close.get("items", []):
                assert f.get("user_decision") == "delete", (
                    f"Close changed the manifest — {f['file_path']!r} "
                    f"user_decision is now {f.get('user_decision')!r} "
                    f"(expected 'delete')"
                )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
