"""Web scenario s39 — preview-panel width persists across a reload (#739).

Re-flip of the desktop-only s39_window_geometry_persist, which was SKIP on
the web (#688 — a browser tab has no OS-controlled window position/size or
per-app geometry-persistence contract; the window chrome belongs to the
browser, not the app). The web's per-machine UI-chrome analog for THIS
scenario's other half — the splitter/floor guard — is the preview-pane width,
now resizable and persisted (#739). This mirrors the #685 s47 pattern
end-to-end through real user gestures:

  1. Drag the preview-resize handle left by a known delta and assert the
     rendered preview-pane width grew.
  2. Assert the new width was written to localStorage under key "panelWidths"
     (the design doc's exact s39 assertion shape — NOT window geometry).
  3. ``page.reload()`` — the web cross-launch boundary — then re-load the same
     manifest.
  4. Assert the preview pane restores to the resized width (the restore path
     reads localStorage at store creation; nothing on manifest load resets it).
  5. Assert the localStorage key survives the reload.

Per-scenario isolation is free here: each scenario runs in its own Playwright
browser context, so localStorage starts empty and cannot leak between
scenarios (no server-side reset needed, unlike the shared settings.json in
s23a/s23b).

Parity note (web-port-tech-design.md:2485, needs_rework category): the
assertion weakens from screen-pixel window position to "preview panel width
survives page.reload()" — see qa/web/scenario_map.yml notes for this entry.

Desktop source: qa/scenarios/s39_window_geometry_persist.py
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
from qa.web.testid_constants import PREVIEW_PANE, PREVIEW_RESIZE_HANDLE

_REPO = Path(__file__).resolve().parents[3]
_SRC = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_STORAGE_KEY = "panelWidths"

# Drag the handle LEFT (widens the preview pane — see App.tsx's
# handlePreviewResizeStart) by this far; assert the pane grew by clearly more
# than noise. Tolerance covers sub-pixel rounding between the drag delta, the
# store's Math.round, and the rendered boundingBox.
_DELTA_PX = 100
_MIN_GROWTH_PX = 40
_TOL_PX = 6


def _preview_width(page) -> float:
    box = page.get_by_test_id(PREVIEW_PANE).bounding_box()
    if box is None:
        raise AssertionError("preview pane has no bounding box")
    return box["width"]


def _wait_rows(page, n: int = 5) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('[data-testid^=\"row-file-\"]').length === n",
        arg=n,
        timeout=20_000,
    )


def run(*, base_url: str) -> None:
    """Scan -> drag-resize the preview pane -> reload -> assert width restores."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s39_")
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

            width_before = _preview_width(page)
            print(f"probe_status: s39 preview width before={width_before}")

            # ── Drag the preview-resize handle LEFT by _DELTA_PX ─────────────
            handle = page.get_by_test_id(PREVIEW_RESIZE_HANDLE)
            hb = handle.bounding_box()
            if hb is None:
                raise AssertionError("preview-resize handle has no bounding box")
            cx = hb["x"] + hb["width"] / 2
            cy = hb["y"] + hb["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx - _DELTA_PX, cy, steps=8)
            page.mouse.up()
            page.wait_for_timeout(150)

            width_after = _preview_width(page)
            print(f"probe_status: s39 preview width after drag={width_after}")
            if width_after < width_before + _MIN_GROWTH_PX:
                failures.append(
                    f"resize drag did not widen the preview pane: before={width_before} "
                    f"after={width_after} (expected +>{_MIN_GROWTH_PX}px) — the "
                    f"preview-resize-handle drag wiring is broken."
                )

            persisted = page.evaluate(
                "(key) => { const v = localStorage.getItem(key); "
                "return v ? (JSON.parse(v).preview ?? null) : null; }",
                _STORAGE_KEY,
            )
            print(f"probe_status: s39 persisted preview width={persisted}")
            if persisted is None or abs(persisted - width_after) > _TOL_PX:
                failures.append(
                    f"resized width not persisted to localStorage['{_STORAGE_KEY}']: "
                    f"persisted={persisted} after={width_after}."
                )

            # ── Reload = cross-launch boundary; re-load the same manifest ──
            page.reload()
            load_manifest(page, db_path)
            _wait_rows(page)
            page.wait_for_timeout(150)

            width_restored = _preview_width(page)
            print(f"probe_status: s39 preview width restored={width_restored}")
            if abs(width_restored - width_after) > _TOL_PX:
                failures.append(
                    f"width not restored after reload: restored={width_restored} "
                    f"!= resized={width_after} (±{_TOL_PX}) — the panel-width "
                    f"hydration from localStorage on store creation regressed, "
                    f"or something resets widths on manifest load."
                )

            still = page.evaluate("(key) => localStorage.getItem(key)", _STORAGE_KEY)
            if not still:
                failures.append(
                    f"localStorage['{_STORAGE_KEY}'] was wiped by the reload."
                )

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            raise AssertionError("; ".join(failures))
        print("scenario: s39_layout_persist DONE")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
