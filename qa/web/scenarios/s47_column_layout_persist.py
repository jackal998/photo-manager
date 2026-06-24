"""Web scenario s47 — result-tree column width persists across a reload (#685).

Ported from the Qt s47_column_layout_persist. The web result tree's column
widths are held in the resultView store slice and persisted to localStorage
(frontend/src/lib/columnWidths.ts) — the per-browser analog of the desktop's
per-machine window_state.ini. This scenario does the FULL round-trip end-to-end
through real user gestures (web mouse-drag is reliable in Playwright, so unlike
the Qt s47 we do NOT need to forge a saved-state blob via a sidecar):

  1. Drag the File Name column's resize handle right by a known delta and assert
     the rendered header width grew.
  2. Assert the new width was written to localStorage.
  3. ``page.reload()`` — the web cross-launch boundary (localStorage survives a
     reload but the loaded manifest does not) — then re-load the same manifest.
  4. Assert the File Name header restores to the resized width (the restore path
     reads localStorage at store creation; nothing on manifest load resets it).
  5. Assert the localStorage key survives the reload (the desktop "closeEvent
     must not wipe column_header" check).

Per-scenario isolation is free here: each scenario runs in its own Playwright
browser context, so localStorage starts empty and cannot leak between scenarios
(no server-side reset needed, unlike the shared settings.json in s23a/s23b).

Desktop source: qa/scenarios/s47_column_layout_persist.py
Fixture:        qa/sandbox/near-duplicates
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    load_manifest,
    open_scan_dialog,
    set_output_path,
    start_scan,
    wait_manifest_loaded,
)
from qa.web.testid_constants import col_header_testid, col_resize_testid

_REPO = Path(__file__).resolve().parents[3]
_SRC = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_STORAGE_KEY = "pm.result-tree.column-widths.v1"

# Drag the handle this far right; assert the column grew by clearly more than
# noise. Tolerance covers sub-pixel rounding between the drag delta, the
# store's Math.round, and the rendered boundingBox.
_DELTA_PX = 120
_MIN_GROWTH_PX = 50
_TOL_PX = 6


def _header_width(page, col_id: str) -> float:
    box = page.get_by_test_id(col_header_testid(col_id)).bounding_box()
    if box is None:
        raise AssertionError(f"header cell {col_id!r} has no bounding box")
    return box["width"]


def _wait_rows(page, n: int = 5) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('[data-testid^=\"row-file-\"]').length === n",
        arg=n,
        timeout=20_000,
    )


def run(*, base_url: str) -> None:
    """Scan → drag-resize File Name → reload → assert width restores."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s47_")
    db_path = os.path.join(tmpdir, "manifest.db")
    failures: list[str] = []
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)
            add_scan_source(page, _SRC, idx=0)
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)
            _wait_rows(page)

            width_before = _header_width(page, "name")
            print(f"probe_status: s47 File Name width before={width_before}")

            # ── Drag the File Name resize handle right by _DELTA_PX ─────────
            handle = page.get_by_test_id(col_resize_testid("name"))
            hb = handle.bounding_box()
            if hb is None:
                raise AssertionError("File Name resize handle has no bounding box")
            cx = hb["x"] + hb["width"] / 2
            cy = hb["y"] + hb["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx + _DELTA_PX, cy, steps=8)
            page.mouse.up()
            page.wait_for_timeout(150)

            width_after = _header_width(page, "name")
            print(f"probe_status: s47 File Name width after drag={width_after}")
            if width_after < width_before + _MIN_GROWTH_PX:
                failures.append(
                    f"resize drag did not widen File Name: before={width_before} "
                    f"after={width_after} (expected +>{_MIN_GROWTH_PX}px) — the "
                    f"resize-handle drag wiring is broken."
                )

            persisted = page.evaluate(
                "(key) => { const v = localStorage.getItem(key); "
                "return v ? (JSON.parse(v).name ?? null) : null; }",
                _STORAGE_KEY,
            )
            print(f"probe_status: s47 persisted name width={persisted}")
            if persisted is None or abs(persisted - width_after) > _TOL_PX:
                failures.append(
                    f"resized width not persisted to localStorage: "
                    f"persisted={persisted} after={width_after}."
                )

            # ── Reload = cross-launch boundary; re-load the same manifest ──
            page.reload()
            load_manifest(page, db_path)
            _wait_rows(page)
            page.wait_for_timeout(150)

            width_restored = _header_width(page, "name")
            print(f"probe_status: s47 File Name width restored={width_restored}")
            if abs(width_restored - width_after) > _TOL_PX:
                failures.append(
                    f"width not restored after reload: restored={width_restored} "
                    f"!= resized={width_after} (±{_TOL_PX}) — the column-width "
                    f"hydration from localStorage on store creation regressed, "
                    f"or something resets widths on manifest load."
                )

            still = page.evaluate("(key) => localStorage.getItem(key)", _STORAGE_KEY)
            if not still:
                failures.append(
                    "localStorage column-widths key was wiped by the reload."
                )

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            raise AssertionError("; ".join(failures))
        print("scenario: s47_column_layout_persist DONE")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
