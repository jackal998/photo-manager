"""Web scenario s27 — re-scan while the manifest has pending decisions (#142).

Ported from qa/scenarios/s27_rescan_confirm.py (Qt UIA).

Qt intent:
  - Scan the 5-JPEG near-duplicates fixture; close & load; set a decision on
    one row. Open the scan dialog and click Start Scan. Because the loaded
    manifest has un-executed decisions, a "Discard pending decisions?" prompt
    appears (Yes/No, body states the pluralized count).
  - Branch A (No / Cancel): the scan does NOT run; the manifest is intact and
    the scan dialog stays open.
  - Branch B (Yes / Discard): add a second decision, re-open, Start Scan,
    confirm Discard — the scan runs, the manifest is rebuilt, and the staged
    decisions are cleared (the destructive behaviour the user opted into).

Web slice:
  1. tmpdir copy of all 5 near-duplicate fixtures; db under tmpdir.
     run_scan([tmpdir]) -> manifest loaded with 0 pending decisions.
  2. Set 'delete' on neardup_00_q95.jpg via the row context menu
     (CTX_SET_ACTION_DELETE) -> 1 pending decision (verified via GET /api/manifest).
  3. BRANCH A — open scan dialog, configure the source, click Start Scan ->
     the rescan-confirm gate (SCAN_RESCAN_CONFIRM_DIALOG) appears; its body
     carries "1 pending decision". Click Cancel (SCAN_RESCAN_CONFIRM_CANCEL):
       - the confirm dismisses,
       - the scan dialog STAYS OPEN (asserted) — true Qt parity, see below,
       - GET /api/manifest is unchanged: still 1 pending decision, 5 files
         (a re-scan would have rebuilt the manifest and cleared the decision,
         so an unchanged manifest proves the scan did not run).
  4. Dismiss the scan dialog; set 'delete' on neardup_01_q88.jpg -> 2 pending.
  5. BRANCH B — re-open, configure, Start Scan -> the gate reappears with body
     "2 pending decisions". Click Discard & Rescan (SCAN_RESCAN_CONFIRM_DISCARD):
     the scan runs and the scan dialog closes on completion. GET /api/manifest
     shows the rebuilt manifest: 0 pending decisions, 5 files.
  6. finally: shutil.rmtree tmpdir.

Web/Qt parity note (the build choice that earns this scenario):
  The rescan-confirm is a nested Radix modal portaled over the open scan dialog
  — the same shape that, in s34, let dismissing the lock-confirm ALSO close the
  Execute dialog (a documented web divergence). Here the FE was BUILT for this
  port (ScanDialog.tsx), so the scan dialog's onInteractOutside/onEscapeKeyDown
  guards were extended to keep it open while the confirm is up (same treatment
  the dialog already gives its nested FsBrowser picker). The result is exact Qt
  parity: Cancel closes only the confirm and the scan dialog remains — no
  divergence to document this time.

Web ordering note:
  Qt reads the pending-decision count from the SQLite manifest and prompts from
  MainWindow._confirm_no_pending_decisions. The web reads it from the loaded
  manifest in the Zustand store (ScanDialog's pendingDecisionCount selector) and
  gates inside ScanDialog.handleStart. The count is verified here through GET
  /api/manifest (the persisted authority the store mirrors).
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
    open_scan_dialog,
    add_scan_source,
    set_output_path,
    start_scan,
    right_click_row,
    click_context_item,
    dismiss_modal_overlays,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    SCAN_DIALOG,
    SCAN_RESCAN_CONFIRM_DIALOG,
    SCAN_RESCAN_CONFIRM_CANCEL,
    SCAN_RESCAN_CONFIRM_DISCARD,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

# The two rows the scenario stages decisions on (highest-quality first, matching
# the Qt s27 targets). Both live in the near-duplicate fixture.
ROW_FIRST = "neardup_00_q95.jpg"
ROW_SECOND = "neardup_01_q88.jpg"

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
    """Flatten all file items from all groups (key is "items", per s34)."""
    items: list[dict] = []
    for group in manifest.get("groups", []):
        items.extend(group.get("items", []))
    return items


def _decision_count(manifest: dict) -> int:
    """Number of rows with a non-empty user_decision (the gate's predicate)."""
    return sum(
        1
        for item in _extract_items(manifest)
        if (item.get("user_decision") or "") != ""
    )


def _group_id_for(manifest: dict, basename: str) -> str:
    """Return str(group_number) of the group containing ``basename``."""
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            if Path(item["file_path"]).name == basename:
                return str(group["group_number"])
    raise AssertionError(
        f"basename {basename!r} not found in manifest groups; "
        f"present={[Path(i['file_path']).name for i in _extract_items(manifest)]}"
    )


def _poll_manifest(base_url: str, db_path: str, *, expect_decisions: int,
                   attempts: int = 40, delay: float = 0.25) -> dict:
    """GET the manifest until its decision count == expect_decisions.

    The re-scan rewrites the manifest DB; the SSE 'finished' event (which
    closes the scan dialog we already waited on) is emitted after the write, so
    a single read normally suffices. The short poll absorbs any micro-lag
    between dialog-close and the DB being readable with the new state.
    """
    last = _get_manifest(base_url, db_path)
    for _ in range(attempts):
        if _decision_count(last) == expect_decisions:
            return last
        time.sleep(delay)
        last = _get_manifest(base_url, db_path)
    return last


# ---------------------------------------------------------------------------
# UI helper
# ---------------------------------------------------------------------------


def _configure_and_start(page, source_dir: str, db_path: str) -> None:
    """Open the scan dialog, fill the single source + output, click Start.

    add_scan_source/set_output_path use Playwright .fill() (replace), so this
    is idempotent across re-opens regardless of what the dialog auto-loaded
    from persisted settings.
    """
    open_scan_dialog(page)
    add_scan_source(page, source_dir, idx=0)
    set_output_path(page, db_path)
    start_scan(page)


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Cancel keeps decisions + no scan; Discard rebuilds the manifest."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s27_")
    db_path = os.path.join(tmpdir, "s27_manifest.db")
    try:
        copied_paths: list[str] = []
        for basename in _ALL_BASENAMES:
            dst = os.path.join(tmpdir, basename)
            shutil.copy(str(_NEAR_DUPS_DIR / basename), dst)
            copied_paths.append(dst)
        assert len(copied_paths) == 5, (
            f"Expected 5 fixture copies, got {len(copied_paths)}"
        )

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: initial scan → manifest loaded, no decisions ─────────
            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )
            manifest = _get_manifest(base_url, db_path)
            assert manifest.get("total_files") == 5, (
                f"Expected 5 files after scan, got {manifest.get('total_files')}"
            )
            assert _decision_count(manifest) == 0, (
                "Freshly scanned manifest should carry no pending decisions; "
                f"got {_decision_count(manifest)}"
            )

            # ── Step 2: stage one decision (neardup_00 → delete) ─────────────
            gid_first = _group_id_for(manifest, ROW_FIRST)
            right_click_row(page, row_file_testid(gid_first, ROW_FIRST))
            click_context_item(page, CTX_SET_ACTION_DELETE)

            manifest = _get_manifest(base_url, db_path)
            assert _decision_count(manifest) == 1, (
                "Expected exactly 1 pending decision after Set Action → Delete; "
                f"got {_decision_count(manifest)}"
            )

            # ── Step 3: BRANCH A — Start Scan → confirm → Cancel (no scan) ───
            _configure_and_start(page, tmpdir, db_path)
            confirm = page.get_by_test_id(SCAN_RESCAN_CONFIRM_DIALOG)
            confirm.wait_for(state="visible", timeout=10_000)

            body_a = confirm.inner_text()
            assert "1 pending decision" in body_a, (
                "Rescan-confirm body must state the singular pending count "
                f"('1 pending decision'); got: {body_a[:200]!r}"
            )

            page.get_by_test_id(SCAN_RESCAN_CONFIRM_CANCEL).click()
            confirm.wait_for(state="hidden", timeout=10_000)

            # True Qt parity: Cancel closes ONLY the confirm; the scan dialog
            # stays open (the nested-modal guard in ScanDialog.tsx).
            assert page.get_by_test_id(SCAN_DIALOG).is_visible(), (
                "Scan dialog must remain open after the rescan-confirm Cancel "
                "(Qt parity; nested-modal interact-outside must not close it)"
            )

            # The scan did NOT run — manifest unchanged (a re-scan would have
            # rebuilt the manifest and cleared the decision).
            manifest_after_cancel = _get_manifest(base_url, db_path)
            assert _decision_count(manifest_after_cancel) == 1, (
                "Cancel must not run a scan — the pending decision must survive; "
                f"got decision_count={_decision_count(manifest_after_cancel)}"
            )
            assert manifest_after_cancel.get("total_files") == 5, (
                "Cancel must not run a scan — file count must be unchanged; got "
                f"{manifest_after_cancel.get('total_files')}"
            )

            # ── Step 4: close the dialog, stage a second decision ────────────
            dismiss_modal_overlays(page)
            page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=10_000)

            gid_second = _group_id_for(manifest_after_cancel, ROW_SECOND)
            right_click_row(page, row_file_testid(gid_second, ROW_SECOND))
            click_context_item(page, CTX_SET_ACTION_DELETE)

            manifest = _get_manifest(base_url, db_path)
            assert _decision_count(manifest) == 2, (
                "Expected 2 pending decisions before Branch B; "
                f"got {_decision_count(manifest)}"
            )

            # ── Step 5: BRANCH B — Start Scan → confirm → Discard (scan runs) ─
            _configure_and_start(page, tmpdir, db_path)
            confirm.wait_for(state="visible", timeout=10_000)

            body_b = confirm.inner_text()
            assert "2 pending decisions" in body_b, (
                "Rescan-confirm body must state the pluralized pending count "
                f"('2 pending decisions'); got: {body_b[:200]!r}"
            )

            page.get_by_test_id(SCAN_RESCAN_CONFIRM_DISCARD).click()

            # The re-scan runs; the scan dialog closes on the SSE 'finished'
            # event. Waiting for it to hide is the reliable "re-scan complete"
            # signal (the status-bar text can be identical to the prior scan).
            page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=120_000)

            # Manifest rebuilt → decisions cleared (the destructive behaviour
            # the Discard verdict opted into).
            manifest_after_rescan = _poll_manifest(
                base_url, db_path, expect_decisions=0
            )
            assert _decision_count(manifest_after_rescan) == 0, (
                "Re-scan should rebuild the manifest and clear all pending "
                f"decisions; got {_decision_count(manifest_after_rescan)}"
            )
            assert manifest_after_rescan.get("total_files") == 5, (
                "Re-scan should re-index the same 5 files; got "
                f"{manifest_after_rescan.get('total_files')}"
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
