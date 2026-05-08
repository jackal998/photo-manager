"""Scenario 25 — Right-click on empty tree area / menu bar / unselected row (#124).

Required source: qa/sandbox/near-duplicates (5 files, basenames neardup_NN_qXX.jpg).

Pins the "no menu shown when nothing is selected" behaviour at
``ContextMenuHandler._on_context_menu`` (app/views/handlers/context_menu.py
lines 62-69 at the time of writing) by exercising three right-click
scenarios that should NOT spawn a Qt context menu, plus one positive
control that confirms the probe itself is not broken.

Why this matters: the early-return guards on invalid-index and empty-
selection are intentional today. They're easy to break later — e.g. a
future "Refresh" or "Paste" action wired up at empty-area right-click
would silently start surfacing the menu where it shouldn't. Layer 1
tests don't exercise the QTreeView ↔ ContextMenuHandler wiring (it's
pure GUI plumbing); layer 3 is where this can be locked in.

Branches:
  A. right-click below the last result-tree row → no Qt popup
  B. right-click on the menu bar → no Qt popup (the OS title-bar popup
     for window controls has a different Win32 class and is not asserted
     against)
  C. right-click on a valid file row WITHOUT prior left-click → popup
     MUST appear with row-context items. The handler uses ``indexAt(pos)``
     on the click position rather than ``selectedIndexes()``, so prior
     selection is not required — the cursor's row IS the target. Pin
     this so a future regression that adds a "selection required" guard
     fails loudly. Captures the menu items so a divergence (e.g. items
     list shrinks or relabels) shows up in the diff.
  D. positive control: left-click + right-click on the same row → popup
     MUST appear. Catches the case where the probe is broken (e.g. the
     popup-detection helper has bit-rotted) and previous "no popup"
     branches were really "popup detection failed".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pywinauto.mouse

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]

# Used in branch D's positive control. Any row in the near-duplicates
# fixture works — picking the first by sort order keeps the click coords
# deterministic across runs.
ROW_FOR_CONTROL = "neardup_00_q95.jpg"


def _empty_area_below_last_row(win) -> tuple[int, int]:
    """Return screen (x, y) of empty space below the last result-tree row.

    Strategy: take the result tree's UIA rect, find the bottom-most row's
    rectangle, then click 30 px below that — well within the tree's
    viewport but past any populated row. If 30 px would overflow the
    tree, clamp to ``tree.bottom - 5``.
    """
    tree = _uia._result_tree(win)
    tree_rect = tree.rectangle()
    rows = tree.descendants(control_type="TreeItem")
    visible_rows = [
        r for r in rows
        if r.is_visible() and r.window_text() and r.window_text().strip()
    ]
    if not visible_rows:
        # Empty tree — click middle of viewport.
        return (
            (tree_rect.left + tree_rect.right) // 2,
            (tree_rect.top + tree_rect.bottom) // 2,
        )
    last_bottom = max(r.rectangle().bottom for r in visible_rows)
    cy = min(last_bottom + 30, tree_rect.bottom - 5)
    cx = (tree_rect.left + tree_rect.right) // 2
    return cx, cy


def _menu_bar_anchor(win) -> tuple[int, int]:
    """Return screen (x, y) on the main window's QMenuBar but BETWEEN the
    individual menu items, so the click hits empty bar (not File/Action/
    List/Log itself).

    Two ``MenuBar`` controls exist under MainWindow on Windows — the OS
    title-bar menu and Qt's QMenuBar. Disambiguate by automation_id:
    only Qt's bar has an auto_id starting with ``QApplication``. Picks
    a point ~50 px to the right of the last named menu item; clamps to
    just inside the bar's right edge so the click stays within it.
    """
    qt_bar = None
    for bar in win.descendants(control_type="MenuBar"):
        aid = bar.element_info.automation_id or ""
        if aid.startswith("QApplication"):
            qt_bar = bar
            break
    if qt_bar is None:
        raise RuntimeError(
            "QMenuBar not found among MenuBar descendants of main window"
        )
    bar_rect = qt_bar.rectangle()
    items = [
        m for m in win.descendants(control_type="MenuItem")
        if (m.element_info.automation_id or "").endswith("QAction")
    ]
    if not items:
        return (
            (bar_rect.left + bar_rect.right) // 2,
            (bar_rect.top + bar_rect.bottom) // 2,
        )
    rightmost = max(items, key=lambda m: m.rectangle().right)
    rr = rightmost.rectangle()
    gap_x = rr.right + 50
    if gap_x > bar_rect.right - 5:
        gap_x = bar_rect.right - 5
    cy = (bar_rect.top + bar_rect.bottom) // 2
    return gap_x, cy


def _row_anchor(win, basename: str) -> tuple[int, int]:
    """Pixel center of the named tree row. Re-uses _uia internals."""
    return _uia._row_anchor(win, basename)


def _baseline_popups(pid: int) -> list[int]:
    """Snapshot existing popup hwnds before a probe action."""
    return list(_uia._list_popup_hwnds(pid))


def _new_popup_hwnds(pid: int, baseline: list[int]) -> list[int]:
    """Return popup hwnds that appeared after baseline."""
    return [h for h in _uia._list_popup_hwnds(pid) if h not in baseline]


def _dismiss_any_popup() -> None:
    """Send Esc twice to clear any popup or active menu-bar state."""
    from pywinauto import keyboard
    keyboard.send_keys("{ESC}")
    time.sleep(0.2)
    keyboard.send_keys("{ESC}")
    time.sleep(0.2)


def main() -> int:
    print("scenario: s25_empty_area_context_menu")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    failures: list[str] = []

    # ── Branch A — right-click empty area below last row ──────────────────
    print("step: branch_a_empty_area_below_rows")
    try:
        ax, ay = _empty_area_below_last_row(win)
        tree_rect = _uia._result_tree(win).rectangle()
        print(f"  click_coords=({ax}, {ay}) tree_rect={tree_rect}")
        baseline = _baseline_popups(pid)
        _uia._focus(win)
        pywinauto.mouse.right_click(coords=(ax, ay))
        time.sleep(0.3)
        ok = _uia.assert_no_qt_popup_within(pid, seconds=1.5, baseline=baseline)
        spawned = _new_popup_hwnds(pid, baseline)
        print(f"  no_popup={ok}  spawned_count={len(spawned)}")
        if not ok:
            failures.append(
                f"branch_a: right-click below last row spawned {len(spawned)} "
                f"unexpected popup(s); empty-area context-menu guard regressed"
            )
        _dismiss_any_popup()
    except Exception as exc:
        failures.append(f"branch_a probe failed: {exc!r}")

    # ── Branch B — right-click on menu bar gap ────────────────────────────
    print("step: branch_b_menu_bar_gap")
    try:
        bx, by = _menu_bar_anchor(win)
        print(f"  click_coords=({bx}, {by})")
        baseline = _baseline_popups(pid)
        _uia._focus(win)
        pywinauto.mouse.right_click(coords=(bx, by))
        time.sleep(0.3)
        ok = _uia.assert_no_qt_popup_within(pid, seconds=1.5, baseline=baseline)
        spawned = _new_popup_hwnds(pid, baseline)
        print(f"  no_qt_popup={ok}  spawned_count={len(spawned)}")
        if not ok:
            failures.append(
                f"branch_b: right-click on menu-bar gap spawned {len(spawned)} "
                f"Qt popup(s); menu bar should not host a context menu"
            )
        _dismiss_any_popup()
    except Exception as exc:
        failures.append(f"branch_b probe failed: {exc!r}")

    # ── Branch C — right-click on a valid row WITHOUT prior left-click ────
    # Empirical: popup appears with row-relevant context items because
    # the handler uses indexAt(pos) on the click position, not selectedIndexes.
    # Pinned as positive assertion so a future "require prior selection"
    # change would fail loudly.
    EXPECTED_ITEMS = {
        "Set Action",
        "Open Folder",
        "Set Action by Field/Regex…",
        "Remove from List",
    }
    print("step: branch_c_row_without_prior_selection")
    try:
        cx, cy = _row_anchor(win, ROW_FOR_CONTROL)
        print(f"  click_coords=({cx}, {cy}) row={ROW_FOR_CONTROL!r}")
        baseline = _baseline_popups(pid)
        _uia._focus(win)
        pywinauto.mouse.right_click(coords=(cx, cy))
        time.sleep(0.3)
        spawned = _new_popup_hwnds(pid, baseline)
        if not spawned:
            failures.append(
                "branch_c: right-click on row without prior left-click "
                "did NOT spawn a popup; either Qt's right-click no-longer "
                "selects the row at indexAt(pos), or a new selection-required "
                "guard was added — review intended behaviour"
            )
        else:
            popup = _uia.connect_by_handle(spawned[0])
            items = popup.descendants(control_type="MenuItem")
            titles = {
                (i.window_text() or "").strip() for i in items
                if (i.window_text() or "").strip()
            }
            print(f"  branch_c_items={sorted(titles)}")
            missing = EXPECTED_ITEMS - titles
            if missing:
                failures.append(
                    f"branch_c: popup spawned but missing expected items: "
                    f"{sorted(missing)}; full set: {sorted(titles)}"
                )
        _dismiss_any_popup()
    except Exception as exc:
        failures.append(f"branch_c probe failed: {exc!r}")

    # ── Branch D — positive control: left-click + right-click → popup ─────
    # Without this control, a busted popup-detection path would let A/B/C
    # report "no popup" forever. Re-using right_click_tree_row's helper
    # which is itself depended on by every other context-menu scenario.
    print("step: branch_d_positive_control")
    try:
        baseline = _baseline_popups(pid)
        _uia.left_click_tree_row(win, ROW_FOR_CONTROL)
        _uia.right_click_tree_row(win, ROW_FOR_CONTROL)
        spawned = _new_popup_hwnds(pid, baseline)
        print(f"  positive_popup_count={len(spawned)}")
        if not spawned:
            failures.append(
                "branch_d: positive control found no popup — popup detection "
                "is broken; A/B/C 'no popup' results above are unreliable"
            )
        _dismiss_any_popup()
    except Exception as exc:
        failures.append(f"branch_d probe failed: {exc!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s25_empty_area_context_menu DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
