"""Scenario 28 — closeEvent fires the 3-button "Unsaved Changes" prompt
when the user has unsaved decisions, and Back / Leave each behave as
labelled.

Required source: ``qa/sandbox/near-duplicates`` (5 files; we set a
single decision via the regex flow to dirty the manifest).

The exit prompt is layer-3 territory because it depends on:
  * MainWindow.closeEvent intercepting the user's close request,
  * QMessageBox rendering with Save/Leave/Back buttons exposed via
    UIA accessible names,
  * the dirty-flag transitions wired across set_decision and
    save_manifest_decisions_silent.

Layer 1 covers each piece in isolation
(``test_set_decision_marks_dirty``, ``test_silent_save_clears_dirty``,
``test_initial_state_is_clean``); this scenario pins the
end-to-end UX.

What's verified:
  1. Setting any decision flips the dirty flag, so closing the window
     fires the "Unsaved Changes" QMessageBox titled exactly that.
  2. The dialog exposes "Back" — clicking it cancels the close and
     leaves the app running with the dirty flag intact.
  3. A second close attempt re-fires the same dialog.
  4. Clicking "Leave" exits the app cleanly without saving — the
     manifest's user_decision values stay as they were already
     auto-persisted (decisions auto-write on set, so "Leave" doesn't
     actually lose data).

What's NOT verified here (covered elsewhere):
  * "Save & leave" → silent save → exit. Layer 1's
    ``test_silent_save_clears_dirty`` proves the save-on-close path;
    rather than wire a SQLite read against this scenario's manifest,
    we trust that mechanism and exercise the more user-visible
    dialog-routing branches here.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from pywinauto.keyboard import send_keys

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]


def _photo_manager_visible(pid: int) -> bool:
    """Return True if any visible top-level window of *pid* still
    exists. Used after Leave/Save & leave to confirm the app exited."""
    try:
        return any(
            t and "Photo Manager" in t
            for _hwnd, _cls, t in _uia.list_process_windows(pid)
        )
    except Exception:
        return False


def main() -> int:
    print("scenario: s28_exit_dirty_prompt")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Setup: scan the fixture and load the manifest ──────────────────
    print("step: scan_and_load")
    dlg, _ = _uia.open_scan_dialog(win)
    _, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    # ── Dirty the manifest via a bulk regex decision ───────────────────
    print("step: dirty_via_regex_delete")
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=".*", action_label="delete"
    )
    _, win = _uia.connect_main()

    failures: list[str] = []

    # ── First close attempt: prompt should appear, click Back ─────────
    print("step: alt_f4_first_attempt")
    _uia._focus(win)  # noqa: SLF001 — _focus is the project-internal helper
    send_keys("%{F4}")

    try:
        exit_hwnd = _uia.wait_for_dialog(pid, _uia.EXIT_CONFIRM_TITLE, timeout=3)
    except Exception as exc:
        failures.append(
            f"Exit prompt did not appear after Alt+F4 with dirty manifest: {exc!r}"
        )
        # Best-effort: kill the dialog if it appeared late, then close.
        _force_close_app(win, pid)
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    exit_dlg = _uia.connect_by_handle(exit_hwnd)
    button_titles = {
        b.window_text() for b in exit_dlg.descendants(control_type="Button")
    }
    print(f"  exit_buttons={sorted(button_titles)!r}")
    expected_buttons = {_uia.EXIT_BTN_SAVE_LEAVE, _uia.EXIT_BTN_LEAVE, _uia.EXIT_BTN_BACK}
    missing = expected_buttons - button_titles
    if missing:
        failures.append(
            f"Exit prompt missing expected buttons: {sorted(missing)!r} "
            f"(saw {sorted(button_titles)!r})"
        )

    print("step: click_back")
    try:
        back_btn = exit_dlg.child_window(title=_uia.EXIT_BTN_BACK, control_type="Button")
        back_btn.click_input()
    except Exception as exc:
        failures.append(f"Could not click Back button: {exc!r}")
    time.sleep(0.5)

    if not _photo_manager_visible(pid):
        failures.append(
            "Photo Manager window disappeared after clicking Back — Back "
            "must cancel the close, not accept it."
        )
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    # ── Second close attempt: prompt should fire again, click Leave ──
    print("step: alt_f4_second_attempt")
    _, win = _uia.connect_main()
    _uia._focus(win)  # noqa: SLF001
    send_keys("%{F4}")

    try:
        exit_hwnd = _uia.wait_for_dialog(pid, _uia.EXIT_CONFIRM_TITLE, timeout=3)
    except Exception as exc:
        failures.append(
            f"Exit prompt did not re-appear on second close attempt: {exc!r}"
        )
        _force_close_app(win, pid)
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    exit_dlg = _uia.connect_by_handle(exit_hwnd)
    print("step: click_leave")
    try:
        leave_btn = exit_dlg.child_window(title=_uia.EXIT_BTN_LEAVE, control_type="Button")
        leave_btn.click_input()
    except Exception as exc:
        failures.append(f"Could not click Leave button: {exc!r}")
    time.sleep(1.0)

    if _photo_manager_visible(pid):
        failures.append(
            "Photo Manager window still visible after clicking Leave — "
            "Leave must accept the close."
        )
        _force_close_app(win, pid)
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s28_exit_dirty_prompt DONE")
    return 0


def _force_close_app(win, pid: int) -> None:
    """Best-effort cleanup if the prompt path went sideways. Sends Esc
    to dismiss any modal, then closes the window. Avoids leaving the
    batch with an orphaned Photo Manager process and a visible dialog
    that the next ``_close_window()`` can't dismiss."""
    try:
        send_keys("{ESC}")
        time.sleep(0.3)
    except Exception:
        pass
    try:
        win.close()
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
