"""Scenario 41 — Empty-state primary-action buttons (#137).

Required source: none — the whole point is to test the pre-manifest
state, so the source list is empty and no scan runs from this driver.

#137 surfaced because the first-run empty-state was just a grey hint
label telling the user to use the File menu. New users with no
manifest had to guess that anything was clickable. Fix: two
QPushButton primary actions ("Scan Sources…" and "Open Manifest…")
next to the hint, wired to the same handlers as the File-menu
QActions.

What this driver pins:

  - Both buttons are reachable through UIA from the main-window
    accessibility tree (they live in the central area, not buried
    inside a dialog or hidden behind a layout that UIA can't see).
  - Their labels match the File-menu items exactly (string-equal
    against the i18n catalog entries that the menu uses).
  - Clicking the scan button opens the Scan Sources dialog — same
    end-state as File → Scan Sources… (s17 covers that route).
  - Clicking the open button opens the native Open Manifest file
    picker — same end-state as File → Open Manifest… (s16 covers
    that route). We CANCEL the picker rather than drive a real
    open, because s16 already covers the load flow; here we only
    care that the button reaches the picker.

What this driver does NOT pin:

  - The visibility lifecycle (`refresh_tree` hides the wrapper).
    Layer-1 ``tests/test_empty_state_action_buttons.py`` covers
    that contract; reproducing it end-to-end here would require
    completing a scan, which doubles the scenario runtime for no
    added signal.
  - Styling. The buttons use default Qt styling; pixel placement
    is asserted nowhere on purpose.
"""
from __future__ import annotations

import sys
import time

from pywinauto.keyboard import send_keys

from qa.scenarios import _uia


# Button labels — pulled from translations/en.yml. Kept as literals
# rather than re-translated at runtime because the rest of the suite
# already assumes the en locale (see s37, s38).
SCAN_BUTTON_LABEL = "Scan Sources…"
OPEN_BUTTON_LABEL = "Open Manifest…"

# Title of the native QFileDialog spawned by File → Open Manifest…
# Matches main_window.open_manifest_title in translations/en.yml.
OPEN_MANIFEST_DIALOG_TITLE = "Open Manifest"


def _find_central_button(win, label: str):
    """Return the empty-state QPushButton matching ``label``.

    The File menu has identically-named QActions which appear in UIA
    as MenuItem controls (not Button), so a plain
    ``win.child_window(title=label, control_type="Button")`` is
    unambiguous — but only AFTER we've opened the File menu. Pre-menu
    state, the only Button descendants of the main window with these
    titles are the empty-state ones. We assert that by counting
    matching Buttons; the test fails loudly if a future refactor
    accidentally renders the menu actions as Buttons too.
    """
    matches = [
        b for b in win.descendants(control_type="Button")
        if (b.window_text() or "").strip() == label
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly 1 Button with title {label!r} in the main "
            f"window's empty state; found {len(matches)}: "
            f"{[b.window_text() for b in matches]!r}"
        )
    return matches[0]


def main() -> int:
    print("scenario: s41_empty_state_action_buttons")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Empty-state presence — confirm we're on the pre-manifest state ──
    # If the source list isn't empty or some prior cached state leaked
    # in, the scenario's assumptions don't hold and the rest is
    # meaningless. Use the existing #42 probe to verify.
    print("step: probe_pre_manifest_state")
    state = _uia.read_main_window_state(win)
    print(f"  empty_state_visible={state['empty_state_visible']}")
    print(f"  tree_visible={state['tree_visible']}")
    if not state["empty_state_visible"]:
        print(
            "FAIL: expected the empty-state hint to be visible at startup "
            "(no manifest loaded); got it hidden. Either a prior scenario's "
            "state leaked or configure didn't reset cleanly."
        )
        return 1

    # ── Buttons are present + labelled correctly ────────────────────────
    print("step: find_scan_button")
    scan_btn = _find_central_button(win, SCAN_BUTTON_LABEL)
    print(f"  scan_button_title={scan_btn.window_text()!r}")
    print(f"  scan_button_enabled={scan_btn.is_enabled()}")
    if not scan_btn.is_enabled():
        print(
            f"FAIL: scan button {SCAN_BUTTON_LABEL!r} found but disabled — "
            f"the empty-state contract is that both buttons are immediately "
            f"actionable (#137)."
        )
        return 1

    print("step: find_open_button")
    open_btn = _find_central_button(win, OPEN_BUTTON_LABEL)
    print(f"  open_button_title={open_btn.window_text()!r}")
    print(f"  open_button_enabled={open_btn.is_enabled()}")
    if not open_btn.is_enabled():
        print(
            f"FAIL: open button {OPEN_BUTTON_LABEL!r} found but disabled — "
            f"the empty-state contract is that both buttons are immediately "
            f"actionable (#137)."
        )
        return 1

    # ── Scan button click → Scan Sources dialog opens ───────────────────
    print("step: click_scan_button")
    scan_btn.invoke()
    try:
        scan_hwnd = _uia.wait_for_dialog(
            pid, _uia.SCAN_DIALOG_TITLE, timeout=5
        )
    except TimeoutError:
        print(
            "FAIL: clicking the empty-state Scan Sources… button did not "
            "open the Scan Sources dialog — the button is not wired to "
            "on_scan_sources, or the handler regressed."
        )
        return 1
    print(f"  scan_dialog_hwnd={scan_hwnd}")
    scan_dlg = _uia.connect_by_handle(scan_hwnd)

    print("step: close_scan_dialog")
    _uia.close_scan_dialog_via_close_button(scan_dlg)

    # ── Open button click → native Open Manifest file picker appears ────
    # We don't drive an actual open — s16 covers that flow. Here we
    # only verify the button reaches the same handler, then cancel
    # the picker to keep the scenario filesystem-clean.
    print("step: click_open_button")
    # Re-find the open button — the previous dialog cycle may have
    # invalidated UIA wrappers cached on the main window.
    _, win = _uia.connect_main()
    open_btn = _find_central_button(win, OPEN_BUTTON_LABEL)
    open_btn.invoke()
    try:
        open_hwnd = _uia.wait_for_dialog(
            pid, OPEN_MANIFEST_DIALOG_TITLE, timeout=5
        )
    except TimeoutError:
        print(
            "FAIL: clicking the empty-state Open Manifest… button did not "
            "open the Open Manifest file picker — the button is not wired "
            "to on_open_manifest, or the handler regressed."
        )
        return 1
    print(f"  open_manifest_dialog_hwnd={open_hwnd}")

    print("step: cancel_open_manifest_dialog")
    # Esc cancels the native QFileDialog. send_keys gives the keypress
    # to whatever currently has focus, which is the just-opened picker.
    send_keys("{ESC}")
    # Brief settle so the dialog tears down before the batch runner's
    # close-window logic races against it.
    time.sleep(0.5)

    # Verify the picker actually closed — otherwise the test passes
    # while leaving a modal dialog up, which then chokes _close_window
    # in the batch runner.
    remaining = [
        t for _, _, t in _uia.list_process_windows(pid)
        if t == OPEN_MANIFEST_DIALOG_TITLE
    ]
    if remaining:
        print(
            f"FAIL: Open Manifest picker did not close after Esc — "
            f"remaining={remaining!r}"
        )
        return 1

    # ── Empty-state still visible — neither button mutated state ────────
    # Belt-and-braces: if a future refactor wired the buttons to a
    # handler that also called refresh_tree() (e.g. as a side effect),
    # the empty-state would have disappeared by now even though no
    # manifest was loaded.
    print("step: probe_post_cancel_state")
    _, win = _uia.connect_main()
    state_after = _uia.read_main_window_state(win)
    print(f"  empty_state_visible_after={state_after['empty_state_visible']}")
    if not state_after["empty_state_visible"]:
        print(
            "FAIL: empty-state disappeared after clicking + cancelling the "
            "buttons. Expected the hint + buttons to remain visible because "
            "no manifest was loaded. A button handler is mutating the load "
            "state when it shouldn't."
        )
        return 1

    print("scenario: s41_empty_state_action_buttons DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
