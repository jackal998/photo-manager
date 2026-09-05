"""Web scenario s25 — context menu only on rows, never on empty surfaces.

Ported from qa/scenarios/s25_empty_area_context_menu.py (Qt UIA).

Qt intent (four branches pinning the "no popup on non-row right-clicks" guard):
  A: right-click below the last populated row → no popup.
  B: right-click the empty gap of the menu bar → no popup.
  C: right-click a file row WITHOUT a prior left-click → popup appears with the
     expected items (indexAt drives the menu, not selectedIndexes).
  D: left-click then right-click the same row → popup appears (positive control
     proving A/B "no popup" results are real, not detector failures).

Web slice:
  - The web ContextMenu mounts only when contextMenu.open is true, set solely via
    FileRow.onContextMenu (FileRow.tsx:44-47 → App.handleContextMenu). The
    MAIN_RESULT_TREE viewport (ResultTree.tsx:196) and the MAIN_MENU_BAR
    (MenuBar.tsx) carry NO onContextMenu handler, so right-clicking their empty
    surfaces mounts nothing — Branches A/B are a structural guard.
  - Branches C/D overlap s15; they are kept here as the scenario's own positive
    controls (the detector-sanity half of the Qt invariant).

Qt divergences:
  - "No popup" is asserted as ``get_by_test_id(CONTEXT_MENU).count() == 0`` after
    a settle window (a buggy menu would have mounted synchronously). A browser
    native context menu may appear on empty right-clicks; it has no testid and is
    dismissed with Escape, so it never confounds the assertion.
  - Branch A self-validates that empty space exists below the last row before
    clicking it (the maximised window leaves the viewport taller than 6 rows).
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan, right_click_row
from qa.web.testid_constants import (
    CONTEXT_MENU,
    CTX_SET_ACTION_DELETE,
    MAIN_MENU_BAR,
    MAIN_RESULT_TREE,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_FIRST_MEMBER = "neardup_00_q95.jpg"
_LAST_MEMBER = "neardup_04_q65.jpg"


def _first_group_id(base_url: str, db_path: str) -> str:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        data = json.loads(resp.read())
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def _assert_no_menu(page, *, label: str) -> None:
    """Assert OUR context menu is not mounted (after a settle for any buggy mount)."""
    page.wait_for_timeout(200)
    count = page.get_by_test_id(CONTEXT_MENU).count()
    assert count == 0, f"{label}: context menu must NOT appear on empty surface (count={count})"
    page.keyboard.press("Escape")  # dismiss any browser-native menu


def run(*, base_url: str) -> None:
    """Drive all four branches of the empty-area context-menu guard."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )
            group_id = _first_group_id(base_url, db_path)

            # ── Branch A: right-click the empty area below the last row ──────
            tree = page.get_by_test_id(MAIN_RESULT_TREE)
            tree_box = tree.bounding_box()
            assert tree_box is not None, "result tree has no bounding box"
            last_row = page.get_by_test_id(row_file_testid(group_id, _LAST_MEMBER))
            last_row.wait_for(state="visible", timeout=10_000)
            last_box = last_row.bounding_box()
            assert last_box is not None, "last row has no bounding box"
            tree_bottom = tree_box["y"] + tree_box["height"]
            last_bottom = last_box["y"] + last_box["height"]
            assert tree_bottom > last_bottom + 6, (
                "no empty area below the last row to test (viewport too short); "
                f"tree_bottom={tree_bottom}, last_bottom={last_bottom}"
            )
            empty_x = tree_box["x"] + tree_box["width"] / 2
            empty_y = last_bottom + (tree_bottom - last_bottom) / 2
            page.mouse.click(empty_x, empty_y, button="right")
            _assert_no_menu(page, label="Branch A (tree empty area)")

            # ── Branch B: right-click the empty gap of the menu bar ──────────
            menu_bar = page.get_by_test_id(MAIN_MENU_BAR)
            mb_box = menu_bar.bounding_box()
            assert mb_box is not None, "menu bar has no bounding box"
            # Triggers sit on the left; the far-right strip is empty.
            page.mouse.click(
                mb_box["x"] + mb_box["width"] - 8,
                mb_box["y"] + mb_box["height"] / 2,
                button="right",
            )
            _assert_no_menu(page, label="Branch B (menu-bar gap)")

            # ── Branch C: right-click a row WITHOUT a prior left-click ───────
            row_tid = row_file_testid(group_id, _FIRST_MEMBER)
            right_click_row(page, row_tid)  # waits for CONTEXT_MENU visible
            assert page.get_by_test_id(CTX_SET_ACTION_DELETE).is_visible(), (
                "Branch C: expected the Delete item in the row context menu"
            )
            page.keyboard.press("Escape")
            page.get_by_test_id(CONTEXT_MENU).wait_for(state="hidden", timeout=5_000)

            # ── Branch D: left-click then right-click the same row ───────────
            page.get_by_test_id(row_tid).click()  # left-click selects
            right_click_row(page, row_tid)  # waits for CONTEXT_MENU visible
            assert page.get_by_test_id(CONTEXT_MENU).is_visible(), (
                "Branch D: popup must appear on right-click after a left-click"
            )
            page.keyboard.press("Escape")
            page.get_by_test_id(CONTEXT_MENU).wait_for(state="hidden", timeout=5_000)
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
