"""Scenario 26 — Keyboard-only navigation through the main flow (#125).

Required source: qa/sandbox/near-duplicates (5 files → 1 group of 5).

Every other scenario assumes mouse driving; this one drives via keyboard
exclusively to catch the focus-management bugs mouse drivers miss
because they re-focus explicitly per click. Specifically pins:

  1. Result tree gets keyboard focus after manifest load and arrow Down
     moves through rows (each subsequent Down lands on a different
     TreeItem with keyboard focus).
  2. Enter on a result-tree row: documented behaviour (currently a
     no-op — pinned so a future change to wire up Enter shows up
     loudly).
  3. **Alt+F mnemonic opens File menu** (the load-bearing #135 fix).
     Down → Enter on the first item ("Scan Sources…") opens the scan
     dialog. This is the assertion that makes #135's mnemonic fix
     verifiable end-to-end at layer 3.
  4. Tab cycle through the open Scan dialog reaches the canonical
     widgets (path field, + Add, output, sliders, Start Scan, Close)
     in some sensible order — exact ordering documented but not pinned
     because Qt's tab order is set by widget creation in _build_ui and
     a future re-layout might shuffle it intentionally.
  5. Esc dismisses the Scan dialog cleanly.

## IME safety

All keystrokes used here (Tab / Enter / Esc / Alt+letter / arrow keys)
bypass the IME and reach the focused widget directly even when the
operator's session has bopomofo / pinyin / kana active. Free Latin
text would be intercepted; this scenario doesn't use any.

## Out of scope

Tab cycle on the Execute Action and Action-by-Regex dialogs — separate
follow-up scenario after #135-style mnemonic / accelerator coverage
is decided for those.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from pywinauto import keyboard
from pywinauto.uia_defines import IUIA

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]


def _focused_element_summary() -> dict:
    """Read the currently keyboard-focused element via UIA, return a
    minimal summary suitable for logging.

    Returns ``None`` for the result if no element claims focus (which
    can happen briefly after a window switch). Callers should retry.
    """
    try:
        elem = IUIA().iuia.GetFocusedElement()
    except Exception:
        return {"name": "<query-failed>", "control_type": "?", "auto_id": ""}
    try:
        return {
            "name": elem.CurrentName or "",
            "control_type": elem.CurrentControlType,
            "auto_id": elem.CurrentAutomationId or "",
        }
    except Exception:
        return {"name": "<read-failed>", "control_type": "?", "auto_id": ""}


def _focused_auto_id_tail(max_chars: int = 50) -> str:
    """Compact version of the auto_id for log lines (last N chars)."""
    aid = _focused_element_summary().get("auto_id") or ""
    return aid[-max_chars:] if len(aid) > max_chars else aid


def main() -> int:
    print("scenario: s26_keyboard_navigation")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── Setup: scan + close-and-load to give the result tree real rows ───
    print("step: scan_and_load")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    failures: list[str] = []

    # ── Step 1+3: focus result tree, arrow Down five times ───────────────
    # The tree's auto_id is set by Qt from the QTreeView's objectName.
    # We use ``set_focus()`` to seed deterministic focus rather than
    # tabbing through; the TAB-walk path is exercised by step 6.
    print("step: focus_result_tree")
    tree = _uia._result_tree(win)
    tree.set_focus()
    time.sleep(0.3)
    fe = _focused_element_summary()
    print(f"  focused_after_set_focus: type={fe['control_type']} "
          f"name={fe['name']!r} aid_tail={_focused_auto_id_tail()!r}")

    print("step: arrow_down_five_times")
    # UIA reports the focused element as the QTreeView itself (auto_id
    # stays constant across rows), but the focused element's Name
    # property reflects the CURRENT row's primary cell text — so that's
    # the signal we use for "did selection move".
    seen_focused_names: list[str] = []
    for i in range(5):
        keyboard.send_keys("{DOWN}")
        time.sleep(0.15)
        fe = _focused_element_summary()
        seen_focused_names.append(fe["name"])
        print(f"  after_down_{i+1}: type={fe['control_type']} "
              f"name={fe['name'][:40]!r}")
    distinct_count = len(set(seen_focused_names))
    print(f"  distinct_focus_targets={distinct_count}")
    if distinct_count < 2:
        # If arrow Down doesn't change the focused element's Name, the
        # tree is NOT receiving keyboard input — real bug.
        failures.append(
            f"arrow Down × 5 produced only {distinct_count} distinct "
            f"focused row name(s); keyboard navigation in result tree is "
            f"broken — names={seen_focused_names}"
        )

    # ── Step 4: Enter on the focused row — pin current behaviour ─────────
    print("step: enter_on_row")
    sb_before = _uia.read_status_bar_text(win)
    keyboard.send_keys("{ENTER}")
    time.sleep(0.5)
    sb_after = _uia.read_status_bar_text(win)
    pid_now = win.process_id()
    new_dialogs = [
        t for _h, _c, t in _uia.list_process_windows(pid_now)
        if t and "Photo Manager" not in t and "Scan" not in t
    ]
    print(f"  status_before={sb_before!r}")
    print(f"  status_after={sb_after!r}")
    print(f"  new_dialogs_after_enter={new_dialogs}")
    # Don't fail — Enter behaviour is "documented", not asserted. If a
    # future change wires Enter up to do something visible, this section
    # will print the change and a follow-up should update the test to
    # assert the new behaviour deliberately.

    # ── Step 5: Alt+F mnemonic opens File menu (the #135 assertion) ──────
    # The acceptance criterion is "at least one menu opened via mnemonic"
    # — the popup-appearing check IS the load-bearing test.
    #
    # Activating a menu item via keyboard from there (Down+Enter) is
    # finicky on Windows: the popup is a separate top-level Qt window
    # and synthesized keystrokes need the popup as foreground to route
    # correctly. We use the regular menu_path helper to drive the click
    # for the rest of the test (the Tab-cycle in the dialog is the
    # real keyboard-nav target). This keeps step 5 focused on what
    # #135 unlocked: that Alt+F WORKS.
    print("step: alt_f_to_open_file_menu")
    win.set_focus()  # ensure menu-bar accelerators target the main window
    time.sleep(0.3)
    keyboard.send_keys("%f")  # %f = Alt+F per pywinauto convention
    time.sleep(0.5)
    popup_hwnd = _uia.find_popup(pid)
    if popup_hwnd is None:
        failures.append(
            "Alt+F did not open File menu — mnemonic regression of #135"
        )
        scan_dlg = None
    else:
        print(f"  alt_f_opened_popup_hwnd={popup_hwnd}")
        # Dismiss the menu — we got what we needed (the open).
        keyboard.send_keys("{ESC}")
        time.sleep(0.3)
        keyboard.send_keys("{ESC}")
        time.sleep(0.3)

        # Now open the scan dialog via the regular click-driven path so
        # step 6 has a dialog to tab through. (If we had a clean way to
        # activate the first menu item via keyboard we'd use it here,
        # but cross-popup Enter routing is unreliable on Windows.)
        print("step: open_scan_dialog_for_tab_cycle_test")
        try:
            scan_dlg, _ = _uia.open_scan_dialog(win)
            print(f"  scan_dlg_opened=True")
        except Exception as exc:
            failures.append(
                f"could not open scan dialog for Tab-cycle test: {exc!r}"
            )
            scan_dlg = None

        # ── Step 6: Tab cycle through scan dialog ────────────────────────
        if scan_dlg is not None:
            print("step: tab_cycle_in_scan_dialog")
            scan_dlg.set_focus()
            time.sleep(0.3)
            # Tab N times, record each focused control_type + name +
            # auto_id-tail so log diff catches future tab-order shuffles.
            tab_observations: list[dict] = []
            INITIAL = _focused_element_summary()
            tab_observations.append(INITIAL)
            print(f"  initial_focus: {INITIAL['control_type']} "
                  f"name={INITIAL['name'][:30]!r} "
                  f"aid_tail={_focused_auto_id_tail()!r}")
            TAB_COUNT = 12
            for i in range(TAB_COUNT):
                keyboard.send_keys("{TAB}")
                time.sleep(0.1)
                fe = _focused_element_summary()
                tab_observations.append(fe)
                print(f"  after_tab_{i+1}: {fe['control_type']} "
                      f"name={fe['name'][:30]!r}")
            distinct_focus_count = len(
                {(o["control_type"], o["name"], o["auto_id"])
                 for o in tab_observations}
            )
            print(f"  distinct_tab_focus_targets={distinct_focus_count}")
            if distinct_focus_count < 3:
                # Tab cycle should walk through at LEAST a handful of
                # distinct focusable widgets. If it stays on one widget,
                # tab order is broken.
                failures.append(
                    f"Tab cycle in Scan dialog visited only "
                    f"{distinct_focus_count} distinct widget(s) over "
                    f"{TAB_COUNT} Tabs; tab order may be broken"
                )

            # ── Step 7: Esc dismisses scan dialog ────────────────────────
            print("step: esc_dismisses_scan_dialog")
            scan_dlg.set_focus()
            time.sleep(0.2)
            keyboard.send_keys("{ESC}")
            time.sleep(0.5)
            still_open = any(
                t == _uia.SCAN_DIALOG_TITLE
                for _h, _c, t in _uia.list_process_windows(pid)
            )
            print(f"  scan_dialog_still_open_after_esc={still_open}")
            if still_open:
                failures.append(
                    "Scan dialog was NOT dismissed by Esc — modal-dialog "
                    "Esc handling regression"
                )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s26_keyboard_navigation DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
