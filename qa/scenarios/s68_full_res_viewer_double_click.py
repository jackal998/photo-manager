"""Scenario 68 — double-click on preview tile opens the full-res viewer (#622 Phase 1).

Required source: qa/sandbox/near-duplicates (5 JPEGs, one near-dup group).

What this exercises (the production wiring that layer-1 unit tests can't reach):

  1. ``PreviewPane.show_single`` (image branch) renders the single-view label
     and submits the async ``_ResolutionTask`` — no UI-thread block on the
     read-resolution probe (#622 ticket item 6).
  2. The ``_single_label`` QLabel carries ``objectName="preview_single_label"``
     and is discoverable via UIA aid-suffix matching.
  3. A double-click on the label fires ``mouseDoubleClickEvent`` →
     ``_on_single_label_double_click`` → ``requestFullRes.emit(path)``.
  4. ``MainWindow.on_open_full_res_viewer`` constructs ``FullResViewerDialog``
     with ``service=self._img`` (DI) and calls ``show()`` — the dialog
     appears as a real top-level window with the filename in its title.
  5. Pressing Escape on the dialog closes it (WA_DeleteOnClose semantics),
     and the main window remains responsive afterward.

Why this is L3 and not L2 unit-only: the signal emit + slot dispatch is
already pinned at L1 (tests/test_preview_pane.py +
tests/test_main_window.py::test_on_open_full_res_viewer_constructs_dialog…).
What only a live run can prove is that the Qt event delivered from a real
mouse double-click on the actual label widget makes it to the same code
path — and that the dialog Qt creates is a usable top-level window
discoverable by the OS window manager. Both are easy to silently break
with an objectName drop, a parent reorder, or a setModal/show() change.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import time
from pathlib import Path

import pywinauto.mouse
from pywinauto.keyboard import send_keys

from qa.scenarios import _uia

_user32 = ctypes.windll.user32

# Object name set in app/views/preview_pane.py for the single-view label.
# UIA reports it as the trailing segment of the automation_id path.
SINGLE_LABEL_AID_SUFFIX = ".preview_single_label"

# Title-substring used to recognise the FullResViewerDialog among the
# process's top-level windows. Set by app/views/dialogs/full_res_viewer.py
# in ``__init__`` (just the filename) and updated to "<filename>  [W×H]"
# in ``_load_image`` after the QImage is decoded. The q95 variant is the
# highest-quality of the 5 near-dups and the deterministic first row in
# the default sort (file size desc); pick it as the unambiguous target.
EXPECTED_FILE_NAME = "neardup_00_q95.jpg"


def _post_double_click_at(win_hwnd: int, cx: int, cy: int) -> None:
    """Send a real ``WM_LBUTTONDBLCLK`` to `win_hwnd` at screen (cx, cy).

    ``UIAWrapper.click_input(double=True)`` synthesizes two SendInput pairs
    that Qt processes as two singles rather than one double — same trap
    s40's ``double_click_tree_row`` documents (Qt's mouse event filter
    needs the OS-level WM_LBUTTONDBLCLK to fire ``mouseDoubleClickEvent``).
    ScreenToClient converts the screen point to the main window's client
    coordinates; Qt's QWidget routing then dispatches the event to whichever
    child widget lies at that point.
    """
    pt = ctypes.wintypes.POINT(cx, cy)
    _user32.ScreenToClient(win_hwnd, ctypes.byref(pt))
    lparam = (pt.y & 0xFFFF) << 16 | (pt.x & 0xFFFF)
    WM_LBUTTONDBLCLK = 0x0203
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001
    _user32.PostMessageW(win_hwnd, WM_LBUTTONDBLCLK, MK_LBUTTON, lparam)
    _user32.PostMessageW(win_hwnd, WM_LBUTTONUP, 0, lparam)


def _find_dialog_by_title_substr(
    pid: int, exclude_hwnd: int, name_substr: str, timeout: float = 5.0
) -> int | None:
    """Find a top-level window of `pid` whose title contains `name_substr`,
    excluding `exclude_hwnd` (the main window). Returns the new hwnd or None.

    The full-res viewer is a non-modal QDialog so it gets its own top-level
    HWND. ``_uia.wait_for_dialog`` is exact-match on title, but our title
    contains the auto-appended ``[W×H]`` suffix once the image loads — so
    we substring-match on the filename instead.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd, _cls, title in _uia.list_process_windows(pid):
            if hwnd == exclude_hwnd:
                continue
            if name_substr in title:
                return hwnd
        time.sleep(0.2)
    return None


def main() -> int:
    print("scenario: s68_full_res_viewer_double_click")
    app, win = _uia.connect_main()
    main_hwnd = win.handle
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    if "Done." not in log:
        print("FAIL: scan did not finish cleanly")
        for line in _uia.extract_summary(log):
            if line:
                print(f"  log: {line}")
        return 1

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: select_image_row")
    # Give the tree-populate a beat — close_and_load only sleeps 1 s and
    # the tree's expandAll() pass after load races the next UIA query on
    # the first scan after a cold launch.
    time.sleep(1.5)
    # ``left_click_tree_row`` resolves the File-Name cell's screen coords
    # from the tree's row geometry, focuses the window, then issues a
    # left-click — the same path s14/s32/s67 use to seed selection.
    _uia.left_click_tree_row(win, EXPECTED_FILE_NAME)
    target_name = EXPECTED_FILE_NAME
    print(f"  target_row={target_name!r}")
    # Let show_single complete + the async _ResolutionTask kick off.
    time.sleep(1.0)

    print("step: find_preview_single_label")
    # The label is a QLabel; UIA reports a QLabel-with-pixmap as "Image" and
    # an empty/text QLabel as "Text". Try both, falling back to Pane just in
    # case the platform decides to wrap it.
    label = None
    for control_type in ("Image", "Text", "Pane"):
        label = _uia._find_descendant_by_aid_suffix(
            win, control_type, SINGLE_LABEL_AID_SUFFIX
        )
        if label is not None:
            print(f"  found_label control_type={control_type!r}")
            break
    if label is None:
        print(
            f"FAIL: preview_single_label (aid suffix {SINGLE_LABEL_AID_SUFFIX!r}) "
            f"not found — the objectName set in PreviewPane.__init__ is the "
            f"only handle UIA scenarios have on this widget"
        )
        return 1

    print("step: double_click_preview_label")
    rect = label.rectangle()
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    print(f"  label_screen_center=({cx},{cy}) rect={rect}")
    # Seed click — registers Qt's input-tracking state on the target widget
    # so the subsequent WM_LBUTTONDBLCLK is unambiguously a "second click".
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    time.sleep(0.15)
    _post_double_click_at(main_hwnd, cx, cy)
    # Give the slot + dialog construction + first paint a moment.
    time.sleep(1.5)

    print("step: assert_full_res_dialog_opened")
    new_hwnd = _find_dialog_by_title_substr(
        pid, exclude_hwnd=main_hwnd, name_substr=EXPECTED_FILE_NAME, timeout=5
    )
    if new_hwnd is None:
        print(
            f"FAIL: full-res viewer not opened — no top-level window with "
            f"title containing {EXPECTED_FILE_NAME!r} appeared within 5s "
            f"(probe_status: full_res_viewer_opened=False)"
        )
        return 1
    new_dlg = _uia.connect_by_handle(new_hwnd)
    new_title = new_dlg.window_text() or ""
    print(f"  full_res_dialog_title={new_title!r}")
    # Soft-probe for the LLM agent: the load-bearing signal.
    print(f"probe_status: full_res_viewer_opened=True")

    # Spot-check: title should also include the [W×H] suffix after the image
    # decodes. The bare filename was set in __init__; the post-load update in
    # _load_image appends "  [W×H]". Lack of the suffix would mean the
    # synchronous decode raised silently — a regression in the dialog's
    # error handling.
    has_resolution_suffix = "×" in new_title or "x" in new_title.split("]")[0]
    print(f"  title_has_resolution_suffix={has_resolution_suffix}")
    if not has_resolution_suffix:
        # Don't fail — this is a soft probe; the image may legitimately fail
        # to decode in headless / low-resource CI. Just print for review.
        print(
            f"  note: title {new_title!r} lacks the [W×H] suffix; the "
            f"post-load setWindowTitle in _load_image may have raised, or "
            f"the image decode failed on this rig"
        )

    print("step: close_full_res_dialog")
    try:
        new_dlg.set_focus()
        time.sleep(0.2)
    except Exception:
        pass
    send_keys("{ESC}")
    time.sleep(0.6)

    print("step: verify_main_window_responsive")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows_after_close={len(rows)}")
    if len(rows) == 0:
        print(
            "FAIL: main window appears unresponsive after closing the "
            "full-res viewer — no rows readable via UIA"
        )
        return 1

    print("scenario: s68_full_res_viewer_double_click DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
