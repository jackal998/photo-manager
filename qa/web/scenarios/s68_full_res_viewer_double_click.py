"""Web scenario s68 — double-click preview tile opens the full-res viewer.

Ported from qa/scenarios/s68_full_res_viewer_double_click.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files in one group).
  - Select the row for neardup_00_q95.jpg so the inline preview tile populates.
  - Double-click the inline preview TILE — this opens the full-resolution
    image viewer dialog (a new top-level OS window on the desktop).
  - Assert the viewer opened, its title carries the file's basename, and
    (soft) that the W×H dimensions are shown.
  - Press Escape to close it and confirm the main window stays responsive.

Web-observable assertions (PASS gates):
  1. After double-clicking ``preview-single-image``, ``fullres-dialog`` becomes
     visible and ``fullres-image`` is present — proves PreviewPane.onDoubleClick
     → store.openFullRes wired the viewer up.
  2. The viewer region's text/aria-label contains the selected file's basename
     ("neardup_00_q95.jpg").
  3. SOFT probe: after the full-res image loads, the title shows
     '{w} × {h}' — assert the '×' separator non-fatally (matches the desktop
     scenario, which treats the dimension read as a soft probe).
  4. After Escape, ``fullres-dialog`` is hidden and the main result tree plus a
     known file row are still attached (main window stays responsive).

Qt → web divergences:
  - D1 (input synthesis): the desktop scenario synthesises an OS
    WM_LBUTTONDBLCLK with a priming seed-click (and a window-enlarge step) to
    defeat Qt's "two separate single-clicks" trap on the small preview tile.
    React's ``onDoubleClick`` is unambiguous, so the web port uses Playwright's
    native ``dblclick()`` on the preview tile and drops all the seed-click /
    window-resize plumbing.
  - D2 (which element to double-click): the web ``FileRow`` ALSO carries
    ``onDoubleClick`` → ``onOpenFullRes`` (FileRow.tsx:40-42,57) — a shortcut
    the desktop UI does not expose.  The faithful port deliberately
    double-clicks the PREVIEW TILE (``preview-single-image``), NOT the row, to
    match the desktop intent (double-click the *preview*, not the list item).
    Do NOT "simplify" this to a row dblclick — that would exercise a different
    code path than the desktop scenario verifies.
  - D3 (window vs overlay): the desktop asserts a NEW top-level OS window via
    UIA.  The web full-res viewer is a same-document Radix Dialog overlay
    (position:fixed inset-0); we assert ``fullres-dialog`` visibility instead.
  - D4 (title shape): the desktop title is ``"<filename>  [W×H]"`` (one
    string).  The web title renders the basename and a SEPARATE
    ``{w} × {h}`` span (FullResViewer.tsx:158-167).  We assert the filename
    substring (hard) and soft-probe the '×' separator (matches the desktop's
    soft dimension read).
"""
from __future__ import annotations

import json
import os
import tempfile
import shutil
import urllib.request
import urllib.parse
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    PREVIEW_SINGLE_IMAGE,
    FULLRES_DIALOG,
    FULLRES_IMAGE,
    MAIN_RESULT_TREE,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Matches the desktop scenario's EXPECTED_FILE_NAME — the highest-quality
# member of the near-duplicates group.
_TARGET_BASENAME = "neardup_00_q95.jpg"


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Self-contained urllib helper, matching the convention established by
    s13/s51/s56.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _find_group_id(manifest: dict, basename: str) -> str:
    """Return the str(group_number) of the group that contains ``basename``.

    Near-duplicates is a single group, but the contract asks us to locate the
    target by basename rather than assuming the first group — this keeps the
    row testid correct (and Playwright strict-mode happy) regardless of
    grouping.
    """
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            if Path(file_row["file_path"]).name == basename:
                return str(group["group_number"])
    raise AssertionError(
        f"Target file {basename!r} not found in any manifest group"
    )


def run(*, base_url: str) -> int:
    """Scan near-dups, select target row, double-click preview → assert viewer.

    Returns 0 on success; raises AssertionError on any failed assertion.
    The near-duplicates fixture is read-only and this scenario is
    non-destructive (it never deletes or mutates files), so we scan the
    repo fixture directly and use a tmpdir only for the throwaway .db.
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s68_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Scan the near-duplicates fixture ─────────────────────────────
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Derive the group_id holding the target basename ──────────────
            manifest = _get_manifest(base_url, db_path)
            assert manifest.get("total_groups", 0) >= 1, (
                "Expected at least one group in the near-duplicates manifest"
            )
            group_id = _find_group_id(manifest, _TARGET_BASENAME)

            # ── Select the target row (single click → setSelectedFile) ───────
            row_tid = row_file_testid(group_id, _TARGET_BASENAME)
            row = page.get_by_test_id(row_tid)
            row.wait_for(state="visible", timeout=10_000)
            row.scroll_into_view_if_needed(timeout=10_000)
            row.click()

            # ── Wait for the inline preview tile to populate ─────────────────
            preview_tile = page.get_by_test_id(PREVIEW_SINGLE_IMAGE)
            preview_tile.wait_for(state="visible", timeout=10_000)

            # ── Double-click the PREVIEW TILE (D2: tile, not the row) ────────
            preview_tile.dblclick()

            # ── PASS GATE: the full-res viewer opened ────────────────────────
            fullres_dialog = page.get_by_test_id(FULLRES_DIALOG)
            fullres_dialog.wait_for(state="visible", timeout=10_000)
            page.get_by_test_id(FULLRES_IMAGE).wait_for(state="visible", timeout=10_000)
            print("probe_status: full_res_viewer_opened=True")

            # ── Filename assertion (the viewer identifies the right file) ────
            # The basename appears both in the title span text and in the
            # dialog's aria-label ("Full resolution: <basename>").  Read both:
            # inner_text covers the visible title; the aria-label is the
            # belt-and-braces fallback (FullResViewer.tsx:155,160).
            dialog_text = fullres_dialog.inner_text()
            aria_label = fullres_dialog.get_attribute("aria-label") or ""
            assert (
                _TARGET_BASENAME in dialog_text or _TARGET_BASENAME in aria_label
            ), (
                f"Full-res viewer does not name {_TARGET_BASENAME!r}; "
                f"title text was {dialog_text!r}, aria-label was {aria_label!r}"
            )

            # ── SOFT PROBE: dimensions '{w} × {h}' shown after image load ────
            # The desktop scenario treats the dimension read as a soft probe;
            # mirror that — a missing '×' must not fail the scenario.
            try:
                page.wait_for_function(
                    """(testid) => {
                        const el = document.querySelector(
                            '[data-testid="' + testid + '"]'
                        );
                        return el && el.innerText.includes('\\u00d7');
                    }""",
                    arg=FULLRES_DIALOG,
                    timeout=5_000,
                )
                print("probe_status: full_res_dims_shown=True")
            except Exception:  # noqa: BLE001 — soft probe, never fatal
                print("probe_status: full_res_dims_shown=False")

            # ── Close via Escape (onEscapeKeyDown → closeFullRes) ────────────
            page.keyboard.press("Escape")
            fullres_dialog.wait_for(state="hidden", timeout=5_000)

            # ── Responsiveness: main tree + a known row still attached ───────
            page.get_by_test_id(MAIN_RESULT_TREE).wait_for(
                state="visible", timeout=5_000
            )
            page.get_by_test_id(row_tid).wait_for(state="attached", timeout=5_000)
            print("probe_status: main_window_responsive=True")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
