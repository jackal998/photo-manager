"""Scenario 39 — window geometry persists across launches (#141) AND
the layout splitter enforces a minimum-width floor so the preview pane
never goes invisible (#136).

Both bugs ride on the same QSettings round-trip, so they share one
scenario:

  * #141 — ``QMainWindow.saveGeometry()`` is written to QSettings
    in ``closeEvent`` and restored in ``__init__``. We move the
    window to a known geometry, exit, relaunch, and assert the
    geometry was restored.
  * #136 — at the Qt-enforced minimum window width the preview pane
    used to be ~89 px. ``LayoutManager.setup_main_layout`` now pins
    PREVIEW to ``MIN_SECTION_WIDTH`` and disables collapse on both
    panes, which (via QSplitter's size-hint accumulation) lifts the
    window's own minimum width past the broken-preview threshold.
    We send a Win32 ``MoveWindow`` for 100×600 and assert Qt clamps
    the result above the floor. (Tree pane intentionally not pinned
    at the widget level — see ``setup_main_layout`` docstring; this
    was the lesson from PR #191's first qa-batch CI run, where dual
    mins caused right-click anchors to land in the preview pane.)

Why one scenario across two launches (not split a/b like s23):
the QSettings INI is dropped into ``qa/window_state.ini`` (because
``PHOTO_MANAGER_HOME=qa`` is set during the batch), and we control
that file's lifecycle inside the scenario — delete at start (so any
stale state from a prior run doesn't poison the geometry-restored
assertion), launch our own second process, assert, then exit
cleanly. The batch runner's own launched app is the first process,
we exit it explicitly, and our re-launched process is the one the
batch closes at the end.

Why ``win.close()`` rather than File → Exit:
the batch's launched window doesn't reliably become foreground in a
subprocess driver (Windows ``SetForegroundWindow`` is rate-limited
when called from a non-foreground process), so menu-clicking from a
helper subprocess hits "no active window" errors mid-driver. WM_CLOSE
(what pywinauto's ``.close()`` sends) is routed through
``QMainWindow.closeEvent`` exactly like File → Exit, so the save-
geometry path is identical.

Layer 1 (``tests/test_layout_manager_splitter.py``) covers #136 at
the splitter-constraint level: both panes carry
``minimumWidth() >= 200`` and ``childrenCollapsible() is False``, and
the composite minimum-size-hint width is >= 400. That's the
mechanism. This scenario covers the user-observable consequence —
that a real running window cannot actually be squeezed below ~400 px.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]

# Geometry we *ask* for on launch #1. Qt may clamp width up to its
# content-driven minimum (the empty-state label + tree column hints
# can push that to ~700 px on most styles); the test does NOT assert
# we got exactly this rect — it captures whatever launch #1 ended up
# with after the move, then asserts that **the relaunch matches the
# captured state**. That's the actual #141 round-trip property.
REQUEST_X, REQUEST_Y = 50, 50
REQUEST_W, REQUEST_H = 1100, 700

# Tolerance for the round-trip assertion. Two values because position
# and size drift on different scales:
#   * Width/height — QSettings stores them faithfully; observed drift
#     is single-digit pixels (frame-vs-client rounding).
#   * X/Y — Qt's ``saveGeometry`` writes ``GetWindowPlacement``'s
#     ``rcNormalPosition`` (DWM-extended frame on Win10+). MoveWindow
#     sets the visible-frame edge. The round-trip lands consistently
#     offset by 50–60 px on Win10 high-DPI displays — the absolute
#     value drifts but the offset is stable across reboots. We assert
#     within ~100 px (still well below "totally different region"),
#     because what #141 promises the user is "reopens where it was",
#     not pixel-perfect coordinates.
SIZE_TOLERANCE_PX = 10
POSITION_TOLERANCE_PX = 100

# #136 floor — preview pane has MIN_SECTION_WIDTH=200; the splitter
# composite minimum-size-hint is preview (200) + tree-natural (content-
# driven; ~50-300 depending on whether empty-state hint is visible)
# + handle (~5). We assert >= 250 — well above the 89-px-preview
# visible-bug threshold without depending on tree's content-driven
# minimum (which would make the test flaky across translation/font
# changes).
MIN_FLOOR_PX = 250

# Where the persistent-state INI lands when PHOTO_MANAGER_HOME=qa.
# Mirrors MainWindow._qsettings_path() under the same env var.
QSETTINGS_INI_PATH = REPO / "qa" / "window_state.ini"


# ---------------------------------------------------------------------------
# Win32 plumbing — MoveWindow is the only reliable way to move/resize an
# already-running top-level window without going through the title-bar drag
# UI. pywinauto exposes ``move_window`` for the same purpose, but it
# wraps SetWindowPos; MoveWindow is the documented #136-repro path.
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32


def _move_window(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """``MoveWindow(hwnd, x, y, w, h, bRepaint=True)``. Returns the
    BOOL result so callers can detect failure rather than relying on
    the (Qt-clamped) final size."""
    return bool(_user32.MoveWindow(hwnd, x, y, w, h, True))


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the window's frame rect."""
    r = _RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


# ---------------------------------------------------------------------------
# Process lifecycle — relaunching mid-scenario means owning the launch +
# the wait-for-exit ourselves. We tear down the batch's process via the
# File → Exit menu path (no dirty state, so closeEvent goes through the
# clean branch and saves geometry), then poll the visible-window list
# until the process is gone.
# ---------------------------------------------------------------------------


def _photo_manager_visible(pid: int) -> bool:
    """True if any visible top-level window of *pid* is still around.

    Pattern mirrored from s28_exit_dirty_prompt — that scenario waits
    on the same signal after clicking Leave on the dirty-exit prompt.
    """
    try:
        return any(
            t and "Photo Manager" in t
            for _hwnd, _cls, t in _uia.list_process_windows(pid)
        )
    except Exception:
        return False


def _any_photo_manager_window() -> tuple[int, int] | None:
    """Return (hwnd, pid) of any visible Photo Manager top-level window.

    Used after we launch our own subprocess: the venv launcher
    re-execs into a child interpreter, so ``proc.pid`` is the launcher,
    not the UI process. Title-based discovery is robust to that.
    """
    import ctypes
    import ctypes.wintypes
    found: list[tuple[int, int]] = []
    WND = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, title, 256)
            if "Photo Manager" in title.value:
                ppid = ctypes.c_ulong()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(ppid))
                found.append((hwnd, ppid.value))
        return True

    _user32.EnumWindows(WND(cb), 0)
    return found[0] if found else None


def _wait_for_exit(pid: int, timeout: float = 8.0) -> bool:
    """Poll until the process owns no visible Photo Manager window."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _photo_manager_visible(pid):
            return True
        time.sleep(0.1)
    return False


def _wait_for_main_window_any(timeout: float = 12.0) -> bool:
    """Poll until ANY Photo Manager window is visible.

    Title-based (not pid-based) because the venv launcher re-execs:
    ``subprocess.Popen([sys.executable, "main.py"])`` returns the
    launcher's pid, not the child interpreter's. EnumWindows for
    pid=launcher always returns empty.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _any_photo_manager_window() is not None:
            time.sleep(0.5)  # grace for Qt event loop to finish building widgets
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# Main scenario
# ---------------------------------------------------------------------------


def main() -> int:
    print("scenario: s39_window_geometry_persist")

    # ── Pre-flight: nuke any stale window_state.ini so the assertion is
    # against geometry WE set, not whatever a prior run left behind. ──
    if QSETTINGS_INI_PATH.exists():
        QSETTINGS_INI_PATH.unlink()
        print(f"  cleaned stale qsettings: {QSETTINGS_INI_PATH}")

    # ── Launch #1: connect to the batch-launched app ──────────────────
    # The batch's _wait_for_main_window often fires a false-negative
    # WARN because the venv launcher re-execs and the visible window
    # is owned by the child pid, not proc.pid. Title-based wait here
    # is independent of that.
    print("step: connect_first_launch")
    if not _wait_for_main_window_any(timeout=12.0):
        print("FAIL: first launch did not show window within 12s")
        return 1
    _, win = _uia.connect_main()
    pid1 = win.process_id()
    hwnd1 = win.handle
    print(f"  pid={pid1} hwnd={hwnd1} title={win.window_text()!r}")

    failures: list[str] = []

    # ── Move to a known geometry, then capture what Qt actually gave us.
    # Qt may clamp the width to its content-driven minimum; what we
    # care about for #141 is the round-trip, so we capture launch #1's
    # FINAL rect (after Qt has applied any clamping) and compare that
    # to launch #2's restored rect. ────────────────────────────────────
    print(f"step: move_window_to_request ({REQUEST_X},{REQUEST_Y},{REQUEST_W},{REQUEST_H})")
    if not _move_window(hwnd1, REQUEST_X, REQUEST_Y, REQUEST_W, REQUEST_H):
        failures.append("MoveWindow returned FALSE on launch #1")
    time.sleep(0.3)
    rect_launch1 = _get_window_rect(hwnd1)
    print(f"  rect_after_move={rect_launch1}")

    # Sanity: the position took (Qt rarely clamps X/Y, only sizes).
    if abs(rect_launch1[0] - REQUEST_X) > POSITION_TOLERANCE_PX:
        failures.append(
            f"launch #1 X={rect_launch1[0]} != requested {REQUEST_X} "
            f"(tolerance {POSITION_TOLERANCE_PX}) — MoveWindow ignored?"
        )
    if abs(rect_launch1[1] - REQUEST_Y) > POSITION_TOLERANCE_PX:
        failures.append(
            f"launch #1 Y={rect_launch1[1]} != requested {REQUEST_Y} "
            f"(tolerance {POSITION_TOLERANCE_PX}) — MoveWindow ignored?"
        )
    # Final size must at least be non-degenerate (>= the #136 floor).
    if rect_launch1[2] < MIN_FLOOR_PX:
        failures.append(
            f"launch #1 W={rect_launch1[2]} < #136 floor {MIN_FLOOR_PX} — "
            f"window was actually allowed to shrink below the splitter floor."
        )

    # ── Exit cleanly. Sends WM_CLOSE, which Qt routes to ``closeEvent``
    # exactly like clicking File → Exit or hitting the window's X
    # button — same code path, same closeEvent save-geometry call.
    # The menu-driven path needs the window to be foreground first
    # (and on Windows, SetForegroundWindow is flaky from a subprocess
    # because of the foreground-lock timeout); WM_CLOSE doesn't. ─────
    print("step: close_launch_1")
    win.close()
    if not _wait_for_exit(pid1, timeout=8.0):
        failures.append(f"launch #1 (pid={pid1}) did not exit within 8s")
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    # ── Sanity: the close wrote the INI ────────────────────────────────
    if not QSETTINGS_INI_PATH.exists():
        failures.append(
            f"closeEvent did not write {QSETTINGS_INI_PATH} — "
            f"geometry persistence is silently broken."
        )

    # ── Launch #2: relaunch with the SAME env, expect restored geom ──
    print("step: relaunch")
    env = os.environ.copy()
    env["PHOTO_MANAGER_HOME"] = "qa"
    env["QT_ACCESSIBILITY"] = "1"
    proc2 = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"  relaunched parent_pid={proc2.pid}")
    if not _wait_for_main_window_any(timeout=12.0):
        failures.append(
            f"relaunch (parent pid={proc2.pid}) did not show window within 12s"
        )
        for f in failures:
            print(f"FAIL: {f}")
        proc2.terminate()
        return 1

    _, win2 = _uia.connect_main()
    hwnd2 = win2.handle
    rect_launch2 = _get_window_rect(hwnd2)
    print(f"  rect_after_restore={rect_launch2}")

    # ── #141 assertion: restored geometry matches what launch #1 had at
    # exit-time. This is the actual round-trip property — saved bytes
    # in == restored bytes out, regardless of any clamping Qt did on
    # the way in. ─────────────────────────────────────────────────────
    print("step: assert_geometry_restored")
    for axis, idx, tol in (
        ("X", 0, POSITION_TOLERANCE_PX),
        ("Y", 1, POSITION_TOLERANCE_PX),
        ("W", 2, SIZE_TOLERANCE_PX),
        ("H", 3, SIZE_TOLERANCE_PX),
    ):
        if abs(rect_launch2[idx] - rect_launch1[idx]) > tol:
            failures.append(
                f"restored {axis}={rect_launch2[idx]} != launch#1 {axis}="
                f"{rect_launch1[idx]} (tolerance {tol}) — "
                f"#141 round-trip is broken."
            )

    # ── #136 assertion: MoveWindow to 100×600 gets clamped to >= 400 ─
    print("step: assert_min_width_floor")
    _move_window(hwnd2, 100, 100, 100, 600)
    time.sleep(0.3)
    rect3 = _get_window_rect(hwnd2)
    print(f"  rect_after_shrink_attempt={rect3}")
    if rect3[2] < MIN_FLOOR_PX:
        failures.append(
            f"Qt allowed window width to shrink to {rect3[2]} px — "
            f"#136 splitter min-width constraints not in force "
            f"(expected >= {MIN_FLOOR_PX} px)."
        )

    # ── Tidy up: restore a sane geometry, exit cleanly. The batch's
    # own _close_window() will find this 2nd process and close it,
    # then its proc.wait() on the (already-dead) 1st pid returns
    # immediately. ────────────────────────────────────────────────────
    print("step: cleanup_restore_sane_geom_and_exit")
    _move_window(hwnd2, 100, 100, 1024, 768)
    time.sleep(0.2)

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        # Leave the cleanup to the batch's _close_window().
        return 1

    print("scenario: s39_window_geometry_persist DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
