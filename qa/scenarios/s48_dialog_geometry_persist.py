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
  * ActionDialog (Action → Set Action by Field…) — only after
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
    """Dismiss ActionDialog. #391 dropped the explicit Close button —
    dismissal goes via Esc-key (Qt routes to ``reject()``) through
    the shared ``_uia.close_action_dialog`` helper, which also adds
    a short post-key sleep to let the close animation settle before
    the next geometry-readback step."""
    _uia.close_action_dialog(dlg)
    # Geometry round-trip needs an extra beat beyond the helper's own
    # 0.3s sleep — the geometry blobs are written on the closeEvent
    # path, which runs slightly after the Esc-driven reject().
    time.sleep(0.2)


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

    # ── Probe — Wave 8 C13: ActionDialog splitter state persists ─────
    # The Round 3 close goes through ActionDialog.done(), which now ALSO
    # saves splitter state under QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE
    # (Wave 8 C13). Pre-Wave-8 only the outer-window geometry was saved
    # and the splitter handle reset to the [420, 380] default on every
    # reopen. The probe asserts the new blob actually lands in
    # window_state.ini after a normal close — catches a regression
    # where the save_splitter_state call gets dropped from done().
    print("step: probe_action_dialog_splitter_state_persisted")
    from PySide6.QtCore import QSettings
    ini_path = REPO / "qa" / "window_state.ini"
    if ini_path.exists():
        store = QSettings(str(ini_path), QSettings.IniFormat)
        blob = store.value("geometry/action_dialog_splitter")
        if blob is None:
            print(
                "probe_status: C13-splitter-state-persisted FAIL "
                "(geometry/action_dialog_splitter key missing from "
                f"{ini_path} after ActionDialog close — splitter handle "
                "position will not restore on reopen)"
            )
            failures.append(
                "C13: splitter state not saved to INI after ActionDialog close"
            )
        else:
            print("probe_status: C13-splitter-state-persisted PASS")
    else:
        print(
            f"probe_status: C13-splitter-state-persisted SKIP "
            f"({ini_path} not found — Round 3 above may have already failed)"
        )

    # ── Wave 11 probes — #371 E5 Reset window size affordance ────────
    # E5 (Wave 8) added a "Reset window size" button + Ctrl+0 shortcut
    # in ActionDialog's close row. The button is hidden when the dialog
    # opens in flat layout (no match_fn) and visible in the splitter
    # layout. Clicking it removes the geometry/splitter INI keys, so
    # the next open snaps back to setMinimumSize defaults. Layer-1
    # pins the slot wiring; this probe asserts the UIA-observable
    # surface — button visibility, INI key removal on click,
    # equivalent Ctrl+0 shortcut behavior. Round 3 above has already
    # written geometry/action_dialog* keys to the INI so the reset
    # actually has something to remove.
    print("step: probe_e5_reset_button_visible_in_splitter_layout")
    from PySide6.QtCore import QSettings
    _ini_path = REPO / "qa" / "window_state.ini"

    _, win = _uia.connect_main()
    _probe_dlg, _probe_hwnd = _open_action(win)
    _probe_reset_btn = _uia._find_descendant_by_aid_suffix(
        _probe_dlg, "Button", ".regexResetGeometryButton"
    )
    if _probe_reset_btn is None:
        print("probe_status: E5-reset-button-visible FAIL — regexResetGeometryButton not in UIA tree")
        failures.append("E5: regexResetGeometryButton missing in splitter layout")
    elif not _probe_reset_btn.is_visible():
        print("probe_status: E5-reset-button-visible FAIL — button present but hidden in splitter layout")
        failures.append("E5: Reset window size button hidden when splitter layout active")
    else:
        print("probe_status: E5-reset-button-visible PASS")

    # ---------- Probe E5 button click clears INI ----------
    # Click the button; the INI keys must be removed before the next
    # save (which fires on dialog close).
    print("step: probe_e5_button_click_clears_ini")
    if _probe_reset_btn is not None and _probe_reset_btn.is_visible():
        try:
            _probe_reset_btn.click_input()
            time.sleep(0.4)
            # The reset slot removes the keys directly via QSettings.
            # Read the INI live so we observe the immediate post-click
            # state (close-then-reopen would re-write the keys).
            if _ini_path.exists():
                _store = QSettings(str(_ini_path), QSettings.IniFormat)
                _geom_after_click = _store.value("geometry/action_dialog")
                _split_after_click = _store.value(
                    "geometry/action_dialog_splitter"
                )
                if _geom_after_click is None and _split_after_click is None:
                    print("probe_status: E5-button-click-clears-ini PASS")
                else:
                    print(
                        f"probe_status: E5-button-click-clears-ini FAIL — "
                        f"geom={_geom_after_click!r} "
                        f"splitter={_split_after_click!r} "
                        f"(both should be None after reset)"
                    )
                    failures.append(
                        "E5: INI keys not removed after reset-button click"
                    )
            else:
                print(
                    f"probe_status: E5-button-click-clears-ini SKIP — "
                    f"{_ini_path} does not exist"
                )
        except Exception as _exc:
            print(f"probe_status: E5-button-click-clears-ini FAIL — {_exc!r}")
            failures.append(f"E5: click_input on reset button raised: {_exc!r}")
    # Close the probe dialog before the Ctrl+0 round so the next reopen
    # is a fresh instance with restored geometry to clear.
    try:
        _close_action(_probe_dlg)
    except Exception:
        pass

    # ---------- Probe E5 Ctrl+0 shortcut equivalence ----------
    # Reopen the dialog → MoveWindow it to a non-default size → close
    # (the close writes new geometry keys) → reopen → press Ctrl+0 →
    # assert the INI keys were removed again. Mirrors the button-click
    # probe but exercises the QShortcut wiring instead of the Button
    # clicked signal.
    print("step: probe_e5_ctrl_zero_shortcut")
    _, win = _uia.connect_main()
    _probe_dlg2, _probe_hwnd2 = _open_action(win)
    _initial2 = _get_window_rect(_probe_hwnd2)
    _move_window(
        _probe_hwnd2,
        _initial2[0],
        _initial2[1],
        _initial2[2] + RESIZE_DELTA_PX,
        _initial2[3] + RESIZE_DELTA_PX,
    )
    time.sleep(0.3)
    try:
        _close_action(_probe_dlg2)
    except Exception:
        pass
    # Fresh open — geometry keys should now be present in INI; Ctrl+0
    # should wipe them. We focus the dialog and send the shortcut.
    _, win = _uia.connect_main()
    _probe_dlg3, _probe_hwnd3 = _open_action(win)
    try:
        _uia._focus(_probe_dlg3)
        time.sleep(0.2)
        _probe_dlg3.type_keys("^0")
        time.sleep(0.5)
        if _ini_path.exists():
            _store = QSettings(str(_ini_path), QSettings.IniFormat)
            _geom_after_ctrl0 = _store.value("geometry/action_dialog")
            _split_after_ctrl0 = _store.value(
                "geometry/action_dialog_splitter"
            )
            if _geom_after_ctrl0 is None and _split_after_ctrl0 is None:
                print("probe_status: E5-ctrl-zero-clears-ini PASS")
            else:
                # The Ctrl+0 shortcut shares the slot with the button so
                # this should match the button-click behavior. A FAIL
                # here means the QShortcut isn't wired to _reset_geometry.
                print(
                    f"probe_status: E5-ctrl-zero-clears-ini FAIL — "
                    f"geom={_geom_after_ctrl0!r} "
                    f"splitter={_split_after_ctrl0!r} "
                    f"(both should be None after Ctrl+0)"
                )
                failures.append(
                    "E5: Ctrl+0 shortcut did not clear INI keys — "
                    "QShortcut wiring regressed"
                )
        else:
            print(f"probe_status: E5-ctrl-zero-clears-ini SKIP — {_ini_path} missing")
    except Exception as _exc:
        print(f"probe_status: E5-ctrl-zero-clears-ini FAIL — {_exc!r}")
        failures.append(f"E5: Ctrl+0 send_keys raised: {_exc!r}")
    finally:
        try:
            _close_action(_probe_dlg3)
        except Exception:
            pass

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s48_dialog_geometry_persist DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
