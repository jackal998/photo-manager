"""Scenario 48 — dialog geometry persists across close-and-reopen (#215).

Required source: ``qa/sandbox/near-duplicates`` (5 files, basenames
``neardup_NN_qXX.jpg``). The scan is what loads a manifest so the
ExecuteActionDialog and the live-preview ActionDialog become reachable
— without that, ExecuteActionDialog's Action-menu entry is greyed out
and ActionDialog opens in the flat layout (no save-on-close path).

Companion to s39 (which covers the main-window geometry round-trip
across an app restart). This driver covers the close-and-reopen
round-trip WITHIN the same app session for the three resizable
dialogs:

  * ScanDialog (File → Scan Sources…)
  * ExecuteActionDialog (Action → Execute Action…)
  * ActionDialog (Action → Set Action by Field/Regex…) — only after
    a manifest is loaded, so its preview pane / QSplitter layout is
    in force (the flat layout doesn't persist geometry by design;
    layer-1 pins that contract).

For each dialog: capture initial size → resize via Win32 ``MoveWindow``
→ close → reopen → assert the restored size matches the resized rect
(within tolerance). Win32 ``MoveWindow`` is the same plumbing s39 uses;
``pywinauto.move_window`` would work too but the explicit ctypes call
is the documented path and stays consistent with s39.

What this catches that layer-1 doesn't:
  * The ``done()`` override on each dialog actually fires through
    every close path Qt sends a real running window. Layer-1 invokes
    ``dlg.done(0)`` directly — that pins the SAVE side but the
    runtime-close side (Esc, X button, button-box click) is still
    Qt's job. The X-button path in particular goes through
    ``QCloseEvent`` → ``QDialog::closeEvent`` → ``reject()`` →
    ``done()``, and this scenario is the only place we verify Qt's
    chain doesn't bypass our hook.
  * The QSettings INI is created on disk (not just held in-process)
    so a non-IniFormat regression (Windows registry fallback) would
    fail this round-trip — same failure mode s39 catches for the
    main window.

INI lifecycle:
  * We DON'T nuke ``qa/window_state.ini`` at the start: s48 is the
    first scenario to write the new dialog-geometry keys, but the
    file may already exist holding the main-window geometry that
    earlier scenarios persisted (or that s39's launch-1 wrote on
    exit). Coexistence is the actual product contract — all keys
    share one INI — so leaving prior keys in place is the right
    test posture.
  * Each dialog uses its OWN key (``geometry/scan_dialog`` etc.) so
    the three round-trips inside this scenario don't collide with
    each other or with the main-window key.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]

# Resize tolerance for the round-trip assertion. saveGeometry / restoreGeometry
# round-trip exactly in-process for size, but the X11/Win32 frame can drift
# by a few pixels due to DPI / dwm-extended-frame rounding (same drift s39
# documents). Keep the band tight enough that a real regression — geometry
# silently lost, dialog opens at hardcoded default — fails the assertion.
SIZE_TOLERANCE_PX = 25

# Minimum size delta from the dialog's default — the resize must be
# big enough that a "did the round-trip work" comparison is unambiguous
# even with the tolerance above. 60px wider/taller comfortably exceeds
# 25px tolerance × both axes AND fits inside the GitHub-hosted runner's
# small work area (~1044×788 effective). Previous attempt at 200px
# clamped the resize at the screen edge, after which the restore
# clamped DIFFERENTLY (the dialog reopened higher on screen with less
# room below), producing a false negative in CI shard 5.
RESIZE_DELTA_PX = 60


_user32 = ctypes.windll.user32


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = _RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _move_window(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """Win32 MoveWindow with bRepaint=True. Mirrors s39's helper."""
    return bool(_user32.MoveWindow(hwnd, x, y, w, h, True))


def _round_trip_dialog(
    label: str,
    open_dialog,
    close_dialog,
    failures: list[str],
) -> None:
    """Resize ``open_dialog()`` then close & reopen via the supplied
    callbacks and assert the size came back through.

    ``open_dialog()`` must return ``(dialog_wrapper, hwnd)``.
    ``close_dialog(dialog_wrapper)`` must dismiss the dialog so a
    follow-up ``open_dialog()`` brings up a fresh instance — Qt's
    ``done()`` is what fires our save hook.

    Failures are appended to ``failures`` rather than returned so we
    keep running all three rounds even after one breaks (more
    informative bisect).
    """
    print(f"step: round_trip_{label}_first_open")
    dlg, hwnd = open_dialog()
    initial = _get_window_rect(hwnd)
    print(f"  initial_rect={initial}")

    new_w = initial[2] + RESIZE_DELTA_PX
    new_h = initial[3] + RESIZE_DELTA_PX
    print(f"step: round_trip_{label}_resize_to ({initial[0]},{initial[1]},{new_w},{new_h})")
    if not _move_window(hwnd, initial[0], initial[1], new_w, new_h):
        failures.append(f"{label}: MoveWindow returned FALSE on resize")
        close_dialog(dlg)
        return
    time.sleep(0.3)
    resized = _get_window_rect(hwnd)
    print(f"  resized_rect={resized}")

    # Sanity: the resize actually took. Qt may clamp to setMinimumSize,
    # so the rect comes back >= requested, not necessarily equal — but
    # it MUST differ from `initial` (otherwise our restore-assertion
    # below would be a no-op pass).
    if abs(resized[2] - initial[2]) < SIZE_TOLERANCE_PX and abs(
        resized[3] - initial[3]
    ) < SIZE_TOLERANCE_PX:
        failures.append(
            f"{label}: dialog did not actually grow after MoveWindow — "
            f"initial={initial}, after_resize={resized}. The round-trip "
            f"assertion below would silently pass; aborting this round."
        )
        close_dialog(dlg)
        return

    print(f"step: round_trip_{label}_close")
    close_dialog(dlg)

    print(f"step: round_trip_{label}_reopen")
    dlg2, hwnd2 = open_dialog()
    restored = _get_window_rect(hwnd2)
    print(f"  restored_rect={restored}")

    # The width/height must match the resized state. Position can drift
    # (Win32 frame vs. DWM-extended frame on Win10+); s39 documents the
    # same offset. We assert SIZE round-trip because that's what users
    # notice — "my dialog reopened smaller than I left it".
    if abs(restored[2] - resized[2]) > SIZE_TOLERANCE_PX:
        failures.append(
            f"{label}: restored W={restored[2]} != resized W={resized[2]} "
            f"(tolerance {SIZE_TOLERANCE_PX}) — geometry round-trip broken"
        )
    if abs(restored[3] - resized[3]) > SIZE_TOLERANCE_PX:
        failures.append(
            f"{label}: restored H={restored[3]} != resized H={resized[3]} "
            f"(tolerance {SIZE_TOLERANCE_PX}) — geometry round-trip broken"
        )

    close_dialog(dlg2)


def _open_scan(win):
    return _uia.open_scan_dialog(win)


def _close_scan(dlg):
    _uia.close_scan_dialog_via_close_button(dlg)


def _open_execute(win):
    return _uia.open_execute_action_dialog(win)


def _close_execute(dlg):
    """Click ``Close`` on the Execute Action dialog (the cancel-side
    of its QDialogButtonBox — routes to ``reject()`` which goes
    through ``done()`` and fires our save hook)."""
    btn = next(
        (
            b
            for b in dlg.descendants(control_type="Button")
            if (b.window_text() or "").strip() == "Close"
        ),
        None,
    )
    if btn is None:
        raise RuntimeError("Close button not found on ExecuteActionDialog")
    try:
        btn.invoke()
    except Exception:
        btn.click_input()
    time.sleep(0.5)


def _open_action(win):
    pid = win.process_id()
    _uia.menu_path(win, _uia.MENU_ACTION, _uia.ACTION_BY_REGEX)
    hwnd = _uia.wait_for_dialog(pid, _uia.ACTION_DIALOG_TITLE, timeout=5)
    return _uia.connect_by_handle(hwnd), hwnd


def _close_action(dlg):
    """Click ``Close`` on ActionDialog. ``_find_dialog_button`` picks
    the bottom-most match so we don't fight the title-bar Close button
    (which shares the accessible name on en-US Windows)."""
    btn = _uia._find_dialog_button(dlg, _uia.ACTION_DIALOG_BTN_CLOSE)
    try:
        btn.invoke()
    except Exception:
        btn.click_input()
    time.sleep(0.5)


def main() -> int:
    print("scenario: s48_dialog_geometry_persist")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    failures: list[str] = []

    # ── Round 1: ScanDialog (no manifest needed) ──────────────────────
    _round_trip_dialog(
        "scan_dialog",
        lambda: _open_scan(win),
        _close_scan,
        failures,
    )

    # ── Run a quick scan so a manifest is loaded ─────────────────────
    # ExecuteActionDialog requires a loaded manifest (menu action
    # disabled otherwise). ActionDialog opened from the menu picks up
    # match_fn (and thus the preview-pane / QSplitter layout that
    # saves geometry on close) only when groups exist.
    print("step: scan_for_manifest")
    scan_dlg, _ = _open_scan(win)
    log, elapsed = _uia.run_scan_and_wait(scan_dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    _uia.close_and_load_manifest(scan_dlg)
    # close_and_load_manifest tears down the dialog wrapper; refresh
    # the main-window handle for the menu-driven dialogs below.
    _, win = _uia.connect_main()

    # ── Round 2: ExecuteActionDialog ─────────────────────────────────
    _round_trip_dialog(
        "execute_action_dialog",
        lambda: _open_execute(win),
        _close_execute,
        failures,
    )

    # ── Round 3: ActionDialog (with match_fn — preview pane active) ──
    _round_trip_dialog(
        "action_dialog",
        lambda: _open_action(win),
        _close_action,
        failures,
    )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s48_dialog_geometry_persist DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
