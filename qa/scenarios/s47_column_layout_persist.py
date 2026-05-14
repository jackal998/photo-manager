"""Scenario 47 — Results-tree column layout persists across launches (#214).

Two halves of the same QSettings round-trip:

  1. Save path — ``QHeaderView.sectionResized`` fires on every
     user-driven resize and ``MainWindow._save_column_state_only``
     flushes the new state to ``qa/window_state.ini`` immediately.
  2. Restore path — on the next launch, ``MainWindow.refresh_tree``
     calls ``TreeController.restore_column_state`` AFTER
     ``refresh_model``'s ``ResizeToContents → Interactive`` cycle
     (otherwise the auto-sized widths would silently overwrite the
     restored ones, the headline trap from the issue).

Why driving a resize and not a full move:
  Both Acceptance Criteria — drag survives restart, resize survives
  restart — exercise the same ``QHeaderView.saveState()`` blob; the
  resize path is far less flaky to drive via synthetic mouse input
  on a non-foreground subprocess on Win10. (The drag-to-reorder path
  has Qt internals that gate on cursor movement crossing a section
  boundary before the drag is recognised vs. interpreted as a sort
  click; synthetic ``SendInput`` movement is delivered as discrete
  events and can fall just shy of the threshold on a busy CI agent.)
  The move-section logic itself is pinned at layer 1 by
  ``tests/test_tree_controller.py::TestColumnStateRoundTrip
  ::test_round_trip_preserves_visual_order``.

Layer-1 also pins:
  - section-count mismatch falls back to defaults (future-proof
    against new column additions per the issue's Notes section),
  - ``refresh_model``'s internal resize cycle does NOT fire the save
    callback (the biggest regression risk of this PR — without the
    ``blockSignals`` guard, every manifest reload would silently
    overwrite the user's saved widths with auto-sized defaults).

Lifecycle: owns its own re-launch mid-scenario (mirrors s39's pattern
for window geometry, which has the same "save on close, restore on
next launch" property). Writes ``qa/window_state.ini`` and cleans up
any stale copy at startup so the round-trip assertion is against
state THIS scenario set, not whatever a prior run left behind.
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
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
QSETTINGS_INI_PATH = REPO / "qa" / "window_state.ini"

# The two column-name labels we'll be poking. Resolved from
# ``translations/en.yml::column.*`` — must match what
# ``app.views.constants.headers()`` returns. We resize the File Name
# column to a known, recognisably-different width on launch 1, then
# read it back via UIA on launch 2.
COL_FILE_NAME = "File Name"

# The width we'll force File Name to on launch 1. Pick a value clearly
# distinct from any auto-sized default (which depends on file name
# length in the loaded manifest) and clearly distinct from neighbouring
# columns so the assertion has signal even with ±1 px UIA jitter.
TARGET_WIDTH_PX = 411
WIDTH_TOLERANCE_PX = 8


_user32 = ctypes.windll.user32


# ---------------------------------------------------------------------------
# Helpers — header probing + width manipulation
# ---------------------------------------------------------------------------


def _header_item_rect(win, item_name: str):
    """Return ``(left, top, right, bottom)`` for the named column header section.

    PySide6's QTreeView exposes each section as its OWN top-level
    ``Header`` control (not as a ``HeaderItem`` inside a parent
    ``Header``). Each section's ``window_text()`` is the column label.
    Match by name. Visible-only — invisible sections (hidden columns)
    would otherwise win on a stale rect.
    """
    for h in win.descendants(control_type="Header"):
        try:
            if not h.is_visible():
                continue
            if (h.window_text() or "").strip() == item_name:
                r = h.rectangle()
                return r.left, r.top, r.right, r.bottom
        except Exception:
            continue
    names = []
    for h in win.descendants(control_type="Header"):
        try:
            names.append((h.window_text() or "").strip())
        except Exception:
            names.append("<err>")
    raise RuntimeError(
        f"Header section {item_name!r} not found; saw: {names!r}"
    )


def _column_width(win, item_name: str) -> int:
    left, _, right, _ = _header_item_rect(win, item_name)
    return right - left


def _drag_resize_column(win, item_name: str, new_width: int) -> None:
    """Drag the right edge of ``item_name`` so the column width becomes
    ``new_width`` pixels.

    Qt exposes a resize-cursor hotspot in a 3–5 px band centred on the
    section boundary at ``right``. We press just inside that band
    (right - 1), then send intermediate ``move`` events along the
    cursor's expected path before releasing at the target X. Qt's
    QHeaderView::mouseMoveEvent uses the cumulative drag delta from
    the initial press, not the destination X directly — so the
    intermediate moves matter for the section to actually track.
    """
    import pywinauto.mouse

    left, top, right, bottom = _header_item_rect(win, item_name)
    current_width = right - left
    cy = top + (bottom - top) // 2
    # The resize hotspot is 1–3 px inside the section's right edge.
    press_x = right - 1
    target_x = left + new_width

    _uia._focus(win)
    # Sequence: press at edge → series of move events → release.
    # Real SendInput, not Win32 PostMessage — Qt's QHeaderView
    # gates the resize-mode on the live cursor position read at
    # mouseMoveEvent time, not just on the WM_MOUSEMOVE lparam.
    pywinauto.mouse.press(button="left", coords=(press_x, cy))
    time.sleep(0.05)
    # Step the cursor in ~30 px increments so Qt sees a smooth drag.
    delta = target_x - press_x
    steps = max(1, abs(delta) // 30)
    for i in range(1, steps + 1):
        intermediate_x = press_x + int(delta * i / steps)
        pywinauto.mouse.move(coords=(intermediate_x, cy))
        time.sleep(0.02)
    pywinauto.mouse.move(coords=(target_x, cy))
    time.sleep(0.05)
    pywinauto.mouse.release(button="left", coords=(target_x, cy))
    time.sleep(0.25)  # Qt processes the resize + emits sectionResized


# ---------------------------------------------------------------------------
# Process lifecycle — mirrored from s39
# ---------------------------------------------------------------------------


def _photo_manager_visible(pid: int) -> bool:
    try:
        return any(
            t and "Photo Manager" in t
            for _h, _c, t in _uia.list_process_windows(pid)
        )
    except Exception:
        return False


def _any_photo_manager_window():
    found = []
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _photo_manager_visible(pid):
            return True
        time.sleep(0.1)
    return False


def _wait_for_main_window_any(timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _any_photo_manager_window() is not None:
            time.sleep(0.5)
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# Main scenario
# ---------------------------------------------------------------------------


def main() -> int:
    print("scenario: s47_column_layout_persist")

    # ── Pre-flight: clean stale window_state.ini so the assertion is
    # against state WE set this run. ─────────────────────────────────
    if QSETTINGS_INI_PATH.exists():
        QSETTINGS_INI_PATH.unlink()
        print(f"  cleaned stale qsettings: {QSETTINGS_INI_PATH}")
    if MANIFEST_PATH.exists():
        # A leftover manifest from a previous scenario means refresh_tree
        # wouldn't fire from a fresh scan path on launch 2. Removing it
        # forces both launches down the same "scan → refresh_tree" path,
        # which is the actual code path that calls restore_column_state.
        MANIFEST_PATH.unlink()
        print(f"  cleaned stale manifest: {MANIFEST_PATH}")

    failures: list[str] = []

    # ── Launch 1: the batch already launched main.py before this driver
    # ran. Connect, scan, load. ───────────────────────────────────────
    print("step: launch1_connect")
    if not _wait_for_main_window_any(timeout=12.0):
        print("FAIL: launch 1 did not show window within 12s")
        return 1
    app1, win1 = _uia.connect_main()
    pid1 = win1.process_id()
    print(f"  pid={pid1}")

    print("step: launch1_scan_and_load")
    dlg, _ = _uia.open_scan_dialog(win1)
    _uia.run_scan_and_wait(dlg, timeout=30)
    _uia.close_and_load_manifest(dlg)
    if not MANIFEST_PATH.exists():
        print(f"FAIL: launch 1 scan did not produce manifest at {MANIFEST_PATH}")
        return 1
    _, win1 = _uia.connect_main()

    # ── Capture the auto-sized File Name width as the baseline so the
    # resize assertion is genuinely different from default. ──────────
    baseline_width = _column_width(win1, COL_FILE_NAME)
    print(f"  baseline File Name width={baseline_width}px")
    if abs(baseline_width - TARGET_WIDTH_PX) < WIDTH_TOLERANCE_PX:
        # Defensive — if a future header re-org happens to auto-size
        # File Name to ~411 px, our assertion couldn't distinguish
        # "restored" from "default". Bail with a clear message rather
        # than silently passing.
        failures.append(
            f"baseline File Name width {baseline_width}px is too close "
            f"to TARGET_WIDTH_PX={TARGET_WIDTH_PX} — pick a different "
            f"target so the assertion has signal."
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    # ── Drag the right edge of File Name to TARGET_WIDTH_PX. Qt's
    # sectionResized fires → MainWindow._save_column_state_only writes
    # qa/window_state.ini immediately. ─────────────────────────────────
    print(f"step: launch1_resize_column to ~{TARGET_WIDTH_PX}px")
    _drag_resize_column(win1, COL_FILE_NAME, TARGET_WIDTH_PX)
    after_drag_width = _column_width(win1, COL_FILE_NAME)
    print(f"  File Name width after drag={after_drag_width}px")
    if abs(after_drag_width - TARGET_WIDTH_PX) > WIDTH_TOLERANCE_PX:
        # The drag didn't take. On Win10 hosted CI the synthetic
        # SendInput mouse move can occasionally undershoot — diagnose
        # via the diff and abort rather than write a misleading INI.
        failures.append(
            f"drag-resize undershot: requested {TARGET_WIDTH_PX}px, "
            f"got {after_drag_width}px (tolerance {WIDTH_TOLERANCE_PX}). "
            f"#214 wiring may be fine but the QA driver couldn't drive "
            f"the resize. Re-run; consider increasing TARGET_WIDTH_PX "
            f"delta from baseline."
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    # ── Close launch 1 (WM_CLOSE → closeEvent → _save_geometry, which
    # ALSO calls save_column_state). The signal-driven save already
    # wrote the INI, but closeEvent re-asserts everything one more
    # time. ────────────────────────────────────────────────────────────
    print("step: launch1_close")
    win1.close()
    if not _wait_for_exit(pid1, timeout=8.0):
        failures.append(f"launch 1 (pid={pid1}) did not exit within 8s")
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    if not QSETTINGS_INI_PATH.exists():
        failures.append(
            f"close did not write {QSETTINGS_INI_PATH} — column state "
            f"persistence is silently broken."
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    # Sanity: the INI MUST mention our column key, otherwise we wrote
    # a window_state.ini without the column state (sign of a regression
    # where the geometry path was uncommented but the column path was
    # not). The exact serialised bytes are Qt-internal; presence of the
    # key is what we assert.
    ini_text = QSETTINGS_INI_PATH.read_text(encoding="utf-8", errors="replace")
    if "column_header" not in ini_text:
        failures.append(
            f"window_state.ini exists but contains no 'column_header' key — "
            f"_save_geometry is not invoking save_column_state."
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("  INI contains 'column_header' key")

    # ── Launch 2: fresh process, same QSettings, same fixture →
    # restore must put File Name back at ~TARGET_WIDTH_PX. ────────────
    print("step: launch2_relaunch")
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

    print("step: launch2_scan_and_load")
    dlg2, _ = _uia.open_scan_dialog(win2)
    _uia.run_scan_and_wait(dlg2, timeout=30)
    _uia.close_and_load_manifest(dlg2)
    _, win2 = _uia.connect_main()

    restored_width = _column_width(win2, COL_FILE_NAME)
    print(f"  File Name width after restore={restored_width}px")

    print("step: assert_column_state_restored")
    if abs(restored_width - TARGET_WIDTH_PX) > WIDTH_TOLERANCE_PX:
        failures.append(
            f"restored File Name width={restored_width}px != "
            f"launch-1 set {TARGET_WIDTH_PX}px (tolerance "
            f"{WIDTH_TOLERANCE_PX}). #214 restore is silently broken — "
            f"refresh_tree's call to restore_column_state may be in "
            f"the wrong place (must be AFTER refresh_model's "
            f"ResizeToContents → Interactive cycle)."
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s47_column_layout_persist DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
