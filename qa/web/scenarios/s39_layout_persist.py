"""Web scenario s39 — preview-panel width + viewer geometry persist (#739).

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

Second half (#739 sign-off, 2026-07-17) — the FULL-RES VIEWER is now the web's
real analog of the desktop's movable/resizable window, so the half of the Qt
scenario that had no browser equivalent (window position/size round-trip) now
does have one, on the app's own overlay rather than on browser chrome:

  6. Open the viewer (double-click the preview tile) and drag its TITLE BAR.
     The viewer opens filling the viewport, so the drag un-maximizes it to a
     floating window and moves it — assert both (size shrank, origin moved).
  7. Assert the geometry was written to localStorage under
     ``pm.overlay-geometry.fullres.v1`` (its OWN key — not ``panelWidths``).
  8. ``page.reload()`` + reopen the viewer, and assert the window comes back
     at the saved rect rather than filling the viewport again.

The Execute / Set Action dialogs ride the same mechanism; their round-trip is
covered by s48_dialog_geometry_persist (the Qt companion scenario, re-flipped
from SKIP by the same change).

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
from qa.web.testid_constants import (
    FULLRES_DIALOG,
    FULLRES_IMAGE,
    FULLRES_TITLE_BAR,
    PREVIEW_PANE,
    PREVIEW_RESIZE_HANDLE,
    PREVIEW_SINGLE_IMAGE,
)

_REPO = Path(__file__).resolve().parents[3]
_SRC = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_STORAGE_KEY = "panelWidths"
# Per-surface overlay-geometry key (frontend/src/lib/overlayGeometry.ts).
_GEOMETRY_KEY = "pm.overlay-geometry.fullres.v1"

# Title-bar drag delta for the viewer. Geometry is clamped fully inside the
# viewport and the un-maximized window is 80% of it, so the usable drag range
# is 10% of each axis on each side — this delta stays well inside that.
_MOVE_DX = 90
_MOVE_DY = 40
_GEOM_TOL_PX = 6

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


def _open_full_res_viewer(page) -> None:
    """Select the first file row, then double-click its preview tile."""
    row = page.locator('[data-testid^="row-file-"]').first
    row.wait_for(state="visible", timeout=10_000)
    row.click()
    tile = page.get_by_test_id(PREVIEW_SINGLE_IMAGE)
    tile.wait_for(state="visible", timeout=10_000)
    tile.dblclick()
    page.get_by_test_id(FULLRES_DIALOG).wait_for(state="visible", timeout=10_000)
    page.get_by_test_id(FULLRES_IMAGE).wait_for(state="visible", timeout=15_000)


def _viewer_box(page) -> dict:
    box = page.get_by_test_id(FULLRES_DIALOG).bounding_box()
    if box is None:
        raise AssertionError("full-res viewer has no bounding box")
    return box


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

            # ── #739 second half: full-res viewer window geometry ────────────
            _open_full_res_viewer(page)
            open_box = _viewer_box(page)
            print(
                f"probe_status: s39 fullres default box="
                f"{open_box['x']},{open_box['y']},"
                f"{open_box['width']},{open_box['height']}"
            )

            title = page.get_by_test_id(FULLRES_TITLE_BAR)
            tb = title.bounding_box()
            if tb is None:
                raise AssertionError("full-res title bar has no bounding box")
            tx = tb["x"] + tb["width"] / 2
            ty = tb["y"] + tb["height"] / 2
            page.mouse.move(tx, ty)
            page.mouse.down()
            page.mouse.move(tx + _MOVE_DX, ty + _MOVE_DY, steps=8)
            page.mouse.up()
            page.wait_for_timeout(150)

            moved_box = _viewer_box(page)
            print(
                f"probe_status: s39 fullres moved box="
                f"{moved_box['x']},{moved_box['y']},"
                f"{moved_box['width']},{moved_box['height']}"
            )
            # Dragging a viewport-filling window un-maximizes it AND moves it.
            # Both halves matter: a window that shrinks but stays at 0,0 means
            # the drag delta never reached the geometry.
            if moved_box["width"] >= open_box["width"]:
                failures.append(
                    f"title-bar drag did not un-maximize the viewer: "
                    f"width {open_box['width']} -> {moved_box['width']} — the "
                    f"useOverlayGeometry un-maximize rule regressed."
                )
            if moved_box["x"] <= 0 and moved_box["y"] <= 0:
                failures.append(
                    f"title-bar drag did not move the viewer: origin is still "
                    f"{moved_box['x']},{moved_box['y']} — the drag delta is "
                    f"not reaching the persisted geometry."
                )

            geom = page.evaluate(
                "(key) => { const v = localStorage.getItem(key); "
                "return v ? JSON.parse(v) : null; }",
                _GEOMETRY_KEY,
            )
            print(f"probe_status: s39 fullres persisted={geom}")
            if geom is None:
                failures.append(
                    f"viewer geometry not written to localStorage"
                    f"['{_GEOMETRY_KEY}'] after the drag."
                )
            elif (
                abs(geom["x"] - moved_box["x"]) > _GEOM_TOL_PX
                or abs(geom["y"] - moved_box["y"]) > _GEOM_TOL_PX
            ):
                failures.append(
                    f"persisted viewer geometry {geom} does not match the "
                    f"rendered box {moved_box}."
                )

            # ── Reload = cross-launch boundary; reopen the viewer ────────────
            page.reload()
            load_manifest(page, db_path)
            _wait_rows(page)
            _open_full_res_viewer(page)
            page.wait_for_timeout(150)

            restored_box = _viewer_box(page)
            print(
                f"probe_status: s39 fullres restored box="
                f"{restored_box['x']},{restored_box['y']},"
                f"{restored_box['width']},{restored_box['height']}"
            )
            for axis in ("x", "y", "width", "height"):
                if abs(restored_box[axis] - moved_box[axis]) > _GEOM_TOL_PX:
                    failures.append(
                        f"viewer {axis} not restored after reload: "
                        f"{restored_box[axis]} != {moved_box[axis]} "
                        f"(+-{_GEOM_TOL_PX}) — the geometry hydration from "
                        f"localStorage on open regressed."
                    )

            page.keyboard.press("Escape")
            page.get_by_test_id(FULLRES_DIALOG).wait_for(
                state="hidden", timeout=5_000
            )

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            raise AssertionError("; ".join(failures))
        print("scenario: s39_layout_persist DONE")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
