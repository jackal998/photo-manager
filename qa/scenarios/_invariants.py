"""Cross-scenario UX invariants.

These probes ride along with the existing scenario drivers — they don't
launch the app, they don't add new scenarios. The point is to assert
behaviors the user expects to be consistent across surfaces:

  * The status bar updates to a recognisable shape after every
    manifest-changing action (catches verb / pluralisation drift).
  * Manifest-gated menu items toggle as one set, not piecemeal (catches
    the kind of drift that produced the original two-divergent-lists
    bug fixed in this same change).
  * Destructive confirmation prompts use Yes/No buttons (not OK/Cancel)
    and the body mentions a count.
  * Modals dismiss on Esc.

Each probe prints a single ``inv: <name> ok=<bool> ...`` line to stdout
and returns the bool. Drivers decide whether a False is fatal — most
will just propagate it to the existing FAIL/return-1 path.
"""
from __future__ import annotations

import re
import time
from typing import Iterable

from pywinauto.controls.uiawrapper import UIAWrapper

from qa.scenarios import _uia


# Names of menu items that are manifest-gated. Mirrors
# app.views.components.menu_controller.MANIFEST_ACTIONS — kept here as
# UI-visible labels (what UIA exposes) rather than action keys.
MANIFEST_GATED_MENU_ITEMS: tuple[tuple[str, str], ...] = (
    (_uia.MENU_FILE, _uia.FILE_SAVE_MANIFEST),       # save_manifest
    (_uia.MENU_ACTION, _uia.ACTION_EXECUTE),         # execute_action
    (_uia.MENU_LIST, "Remove from List"),            # remove_from_list
)


def assert_status_bar_matches(
    win: UIAWrapper, regex: str, within_s: float = 2.0
) -> bool:
    """Poll the status bar until *regex* matches, up to *within_s* seconds.

    Returns True if a match was observed. Prints a single ``inv:`` line
    so the LLM agent can read the outcome from driver stdout.
    """
    pattern = re.compile(regex)
    deadline = time.time() + within_s
    last_text = ""
    while time.time() < deadline:
        last_text = _uia.read_status_bar_text(win)
        if pattern.search(last_text):
            print(f"  inv: status_bar_matches r={regex!r} ok=True text={last_text!r}")
            return True
        time.sleep(0.1)
    print(f"  inv: status_bar_matches r={regex!r} ok=False text={last_text!r}")
    return False


def assert_manifest_actions_consistent(
    win: UIAWrapper, expected_enabled: bool
) -> bool:
    """Check every manifest-gated menu item is in the same enabled state.

    The bug class this guards against: one code path enables
    ``remove_from_list`` after manifest load while another path forgets,
    so the user sees inconsistent menu state depending on how they got to
    the manifest. After the MANIFEST_ACTIONS centralization both paths
    must agree.
    """
    discrepancies: list[str] = []
    for menu, item in MANIFEST_GATED_MENU_ITEMS:
        try:
            entries = _uia.probe_menu_items(win, menu)
        except Exception as exc:
            print(f"  inv: manifest_actions_consistent menu={menu!r} probe_failed={exc!r}")
            return False
        match = next((en for (title, en) in entries if title == item), None)
        if match is None:
            discrepancies.append(f"{menu}/{item}=missing")
        elif match is not expected_enabled:
            discrepancies.append(f"{menu}/{item}={match}")
    ok = not discrepancies
    print(
        f"  inv: manifest_actions_consistent expected_enabled={expected_enabled} "
        f"ok={ok} discrepancies={discrepancies}"
    )
    return ok


def assert_destructive_confirm_shape(confirm_dlg: UIAWrapper) -> bool:
    """Verify a destructive-confirm box uses Yes/No (not OK/Cancel) and
    its body text mentions a count.

    Pass *confirm_dlg* — the QMessageBox that's already open. The driver
    is responsible for opening it and dismissing it; this probe just
    inspects the open box.
    """
    button_titles: set[str] = set()
    body_texts: list[str] = []
    try:
        for b in confirm_dlg.descendants(control_type="Button"):
            t = (b.window_text() or "").strip()
            if t:
                button_titles.add(t)
        for child in confirm_dlg.descendants():
            try:
                t = (child.window_text() or "").strip()
                if t and t not in button_titles:
                    body_texts.append(t)
            except Exception:
                continue
    except Exception as exc:
        print(f"  inv: destructive_confirm_shape probe_failed={exc!r}")
        return False
    has_yes = "Yes" in button_titles
    has_no = "No" in button_titles
    body = " ".join(body_texts)
    has_count = bool(re.search(r"\d+", body))
    ok = has_yes and has_no and has_count
    print(
        f"  inv: destructive_confirm_shape ok={ok} "
        f"buttons={sorted(button_titles)} has_count={has_count}"
    )
    return ok


def assert_esc_dismisses(
    win: UIAWrapper, dialog_title: str, within_s: float = 1.5
) -> bool:
    """Press Esc on the foreground app and verify *dialog_title* disappears
    from the top-level window list within *within_s* seconds.

    Caller must have already opened the dialog. After this probe the
    dialog is gone.
    """
    import pywinauto.keyboard as kb

    pid = win.process_id()
    pre = {t for _, _, t in _uia.list_process_windows(pid)}
    if dialog_title not in pre:
        print(f"  inv: esc_dismisses dialog={dialog_title!r} ok=False not_open=True")
        return False
    try:
        kb.send_keys("{ESC}")
    except Exception as exc:
        print(f"  inv: esc_dismisses dialog={dialog_title!r} send_keys_failed={exc!r}")
        return False
    deadline = time.time() + within_s
    while time.time() < deadline:
        post = {t for _, _, t in _uia.list_process_windows(pid)}
        if dialog_title not in post:
            print(f"  inv: esc_dismisses dialog={dialog_title!r} ok=True")
            return True
        time.sleep(0.1)
    print(f"  inv: esc_dismisses dialog={dialog_title!r} ok=False still_open=True")
    return False
