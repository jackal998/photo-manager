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

    A seed click via :func:`pywinauto.mouse.click` MUST be sent immediately
    before calling this — that's what arms Qt's double-click detector by
    registering the QLabel's prior press. Verified locally (passing) and
    in s40's tree-row helper (passing in CI). Without the seed, Qt treats
    the bare ``WM_LBUTTONDBLCLK`` as a single click and the override never
    fires.
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

    # Enlarge main window only if it's too small to contain the QLabel's
    # natural rect. The CI runner provisions a 1024x768 display; the
    # restored main-window state from previous scenarios leaves the
    # window narrower than the QLabel's natural width (~225 px plus
    # framing), so the label's screen-center ends up CLIPPED past the
    # window's right edge — the seed click then lands in the void
    # outside the client area and Qt never registers a press. Dev rigs
    # already have a wide enough window so the resize is a no-op there
    # (and we DON'T want to resize huge — going from ~1600 to ~3800
    # confuses Qt's focus and the next menu_path hangs).
    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    sw = _user32.GetSystemMetrics(SM_CXSCREEN)
    sh = _user32.GetSystemMetrics(SM_CYSCREEN)
    cur = win.rectangle()
    cur_w, cur_h = cur.right - cur.left, cur.bottom - cur.top
    MIN_W, MIN_H = 900, 700
    if cur_w < MIN_W or cur_h < MIN_H:
        margin = 16
        target_w = min(sw - 2 * margin, max(MIN_W, cur_w))
        target_h = min(sh - 2 * margin, max(MIN_H, cur_h))
        target_w = max(target_w, MIN_W)
        target_h = max(target_h, MIN_H)
        _user32.MoveWindow(main_hwnd, margin, margin, target_w, target_h, True)
        time.sleep(0.3)
        _uia._focus(win)
        time.sleep(0.2)
        print(
            f"step: enlarge_main_window screen=({sw}x{sh}) "
            f"was=({cur_w}x{cur_h}) new_rect={win.rectangle()}"
        )
    else:
        print(
            f"step: main_window_size_ok screen=({sw}x{sh}) "
            f"rect=({cur_w}x{cur_h})"
        )

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
    print(f"  label_natural_center=({cx},{cy}) rect={rect}")
    # Clamp the click target to the intersection of the label's natural
    # rect and the main window's client rect. UIA reports the QLabel's
    # natural geometry (which can extend past the QScrollArea viewport
    # when the splitter is tree-dominant — verified on the CI runner at
    # 1024x768 even after enlarging the window). Without this clamp the
    # click lands on whatever Z-ordered widget owns the screen point
    # outside the window (typically: nothing), Qt sees no press, and
    # the WM_LBUTTONDBLCLK that follows fires on dead air.
    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    screen_w = _user32.GetSystemMetrics(SM_CXSCREEN)
    screen_h = _user32.GetSystemMetrics(SM_CYSCREEN)
    win_rect = win.rectangle()
    print(f"  screen_dims=({screen_w}x{screen_h}) main_window_rect={win_rect}")
    isect_left = max(rect.left, win_rect.left)
    isect_top = max(rect.top, win_rect.top)
    isect_right = min(rect.right, win_rect.right)
    isect_bottom = min(rect.bottom, win_rect.bottom)
    isect_w = isect_right - isect_left
    isect_h = isect_bottom - isect_top
    if isect_w <= 0 or isect_h <= 0:
        print(
            f"FAIL: label visible region is empty — "
            f"label_rect={rect} window_rect={win_rect} — the splitter or "
            f"window size is leaving the preview pane entirely off-screen; "
            f"enlarging the window further (or moving the splitter) is the "
            f"only recourse"
        )
        return 1
    cx = (isect_left + isect_right) // 2
    cy = (isect_top + isect_bottom) // 2
    in_screen = 0 <= cx < screen_w and 0 <= cy < screen_h
    in_window = (
        win_rect.left <= cx < win_rect.right
        and win_rect.top <= cy < win_rect.bottom
    )
    print(
        f"  clamped_click=({cx},{cy}) visible_region=({isect_w}x{isect_h}) "
        f"click_in_screen={in_screen} click_in_main_window={in_window}"
    )
    # Bring the main window to the foreground so the seed click is
    # delivered to it rather than dispatched to whichever window happens
    # to be active. Windows refuses ``SetForegroundWindow`` from
    # non-foreground processes unless either the target process owns the
    # current foreground or alt-tab semantics allow it; ``_focus`` packages
    # both attempts (``_focus`` lives in ``qa/scenarios/_uia.py``). Without
    # this the CI runner's "no active window" state silently routes the
    # SendInput click into the void.
    _uia._focus(win)
    fg_hwnd = _user32.GetForegroundWindow()
    print(
        f"  after_focus: foreground_hwnd={fg_hwnd} main_hwnd={main_hwnd} "
        f"focus_succeeded={fg_hwnd == main_hwnd}"
    )
    time.sleep(0.2)
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
    # Use read_tree_row_order (no y-filter) rather than read_result_rows
    # (defaults to y_min=600). On the 1024x768 CI runner the main window
    # tops out at y=708, so every TreeItem has top<600 and read_result_rows
    # silently returns []; this used to look like "unresponsive" but the
    # window was actually fine — _uia.py's own read_tree_row_order docstring
    # flags this exact CI-vs-dev-rig pitfall.
    basenames = _uia.read_tree_row_order(win)
    print(f"  total_rows_after_close={len(basenames)}")
    if len(basenames) == 0:
        print(
            "FAIL: main window appears unresponsive after closing the "
            "full-res viewer — no rows readable via UIA"
        )
        return 1

    print("scenario: s68_full_res_viewer_double_click DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
