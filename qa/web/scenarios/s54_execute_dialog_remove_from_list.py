"""Web scenario s54 — Remove from list (finalize) via the EXECUTE dialog menu.

Ported from qa/scenarios/s54_execute_dialog_remove_from_list.py (Qt UIA).

Qt intent:
  Right-click a file row in the Execute Action dialog, navigate Set Action >
  'remove from list', confirm the resulting QMessageBox ('Remove from List',
  default No) with Yes, and assert exactly one fixture row finalized to
  outcome='ignored' (the row leaves the review list; no file is deleted).

Web slice — cluster B (ExecuteContextMenu + RemoveFromListConfirmDialog):
  The EXECUTE-dialog menu's "Remove from list" FINALIZES the row
  (outcome='ignored' via store.removeFromList → POST /api/remove), exactly like
  Qt's execute-dialog single-row path, gated by a confirm sheet
  (EXECUTE_REMOVE_CONFIRM, default = No). (The result-tree menu's "Remove from
  list" also finalizes since #694, but without a confirm and over the whole
  multi-selection — see s20.) After Yes the row
  disappears from the manifest entirely (load() filters WHERE outcome=''),
  while the file stays untouched on disk — the 'ignored' (not 'deleted')
  contract.

Setup note (the s13 convention): /api/remove validates manifest_path under the
allowed roots, and the only registered roots are the scanned files' folders.
So — like s13's destructive execute — the fixtures are COPIED into a tmpdir and
the manifest.db is written INTO that same tmpdir, making the db's folder a
registered root. (s53's lock/decision and s30's bulk-decide skip the
manifest_path guard, so they scan the repo fixture dir directly.)

Web assertion (no SQLite coupling): the finalized row is GONE from GET
/api/manifest AND its copied file still EXISTS on disk — observable proof of
outcome='ignored' (removed from review, not deleted). The other seeded
decision is untouched.

Qt divergences:
  D1. Manifest read via GET /api/manifest JSON vs Qt SQLite outcome column.
  D2. Decided-rows-only execute tree (#676): the scenario seeds two delete
      decisions via the result-tree menu so rows are present to act on.
  D3. outcome='ignored' is asserted behaviourally (row gone from manifest +
      copied file present on disk) rather than by reading the SQLite column.

Desktop source: qa/scenarios/s54_execute_dialog_remove_from_list.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs, copied per the s13 pattern)
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
    click_context_item,
    open_execute_dialog,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    CTX_SET_ACTION_REMOVE,
    EXECUTE_REMOVE_CONFIRM,
    EXECUTE_REMOVE_CONFIRM_YES,
    execute_row_testid,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

_FIXTURE_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]
_Q88 = "neardup_01_q88.jpg"  # kept (decided delete, stays in the list)
_Q80 = "neardup_02_q80.jpg"  # removed from list (finalize outcome='ignored')


def _manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _by_name(manifest: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = it
    return out


def _await_gone(base_url: str, db_path: str, basename: str, *, timeout: float = 6.0) -> dict:
    """Poll GET /api/manifest until ``basename`` is absent (async remove)."""
    deadline = time.monotonic() + timeout
    rows: dict[str, dict] = {}
    while time.monotonic() < deadline:
        rows = _by_name(_manifest(base_url, db_path))
        if basename not in rows:
            return rows
        time.sleep(0.1)
    return rows


def _await_seeded(base_url: str, db_path: str, *, timeout: float = 6.0) -> dict:
    deadline = time.monotonic() + timeout
    rows: dict[str, dict] = {}
    while time.monotonic() < deadline:
        rows = _by_name(_manifest(base_url, db_path))
        if (
            rows.get(_Q88, {}).get("user_decision") == "delete"
            and rows.get(_Q80, {}).get("user_decision") == "delete"
        ):
            return rows
        time.sleep(0.1)
    return rows


def _first_group_id(base_url: str, db_path: str) -> str:
    data = _manifest(base_url, db_path)
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def run(*, base_url: str) -> None:
    """Finalize one row via the execute menu's Remove-from-list + confirm Yes."""
    # Copy fixtures into a tmpdir and write the db there too (s13 pattern), so
    # the manifest_path falls under a registered allowed root for /api/remove.
    tmpdir = tempfile.mkdtemp(prefix="qa_s54_")
    try:
        for basename in _FIXTURE_BASENAMES:
            shutil.copy(str(_NEAR_DUPS_DIR / basename), os.path.join(tmpdir, basename))
        db_path = os.path.join(tmpdir, "s54_manifest.db")
        q80_copy = os.path.join(tmpdir, _Q80)

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )

            pre = _by_name(_manifest(base_url, db_path))
            assert len(pre) == 5, f"expected 5 fixture rows, got {sorted(pre)}"
            assert os.path.exists(q80_copy), f"copied fixture {_Q80} missing: {q80_copy}"

            gid = _first_group_id(base_url, db_path)

            # ── Seed two delete decisions via the result-tree menu ─────────────
            right_click_row(page, row_file_testid(gid, _Q88))
            click_context_item(page, CTX_SET_ACTION_DELETE)
            right_click_row(page, row_file_testid(gid, _Q80))
            click_context_item(page, CTX_SET_ACTION_DELETE)
            seeded = _await_seeded(base_url, db_path)
            assert seeded.get(_Q88, {}).get("user_decision") == "delete", (
                f"seed q88 delete failed: { {k: seeded[k].get('user_decision') for k in seeded} }"
            )
            assert seeded.get(_Q80, {}).get("user_decision") == "delete", (
                f"seed q80 delete failed: { {k: seeded[k].get('user_decision') for k in seeded} }"
            )

            # ── Open execute dialog; remove q80 via the menu + confirm Yes ─────
            open_execute_dialog(page)
            right_click_row(page, execute_row_testid(gid, _Q80))
            click_context_item(page, CTX_SET_ACTION_REMOVE)  # → confirm sheet

            confirm = page.get_by_test_id(EXECUTE_REMOVE_CONFIRM)
            confirm.wait_for(state="visible", timeout=10_000)
            yes = page.get_by_test_id(EXECUTE_REMOVE_CONFIRM_YES)
            yes.wait_for(state="visible", timeout=5_000)
            yes.click()

            # ── Assert: q80 finalized out of the manifest, file still on disk ──
            post = _await_gone(base_url, db_path, _Q80)
            assert _Q80 not in post, (
                f"{_Q80} should be removed from the review list (outcome='ignored'): "
                f"{sorted(post)}"
            )
            assert len(post) == 4, f"expected 4 rows after remove, got {sorted(post)}"
            assert _Q88 in post and post[_Q88].get("user_decision") == "delete", (
                f"{_Q88} must remain with its delete decision intact: {sorted(post)}"
            )
            assert os.path.exists(q80_copy), (
                f"{_Q80} file must NOT be deleted from disk by remove-from-list "
                f"(outcome='ignored', not 'deleted'): {q80_copy}"
            )
            print(f"probe_status: s54 post={sorted(post)} q80_on_disk={os.path.exists(q80_copy)}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
