"""Shared UIA helpers for /qa-explore scenario drivers.

Names and structure of photo-manager's UI are defined in source — buttons,
menu items, dialog titles, automation IDs are static. Encode them here as
constants so individual scenario drivers stay short and the agent doesn't
re-discover "what is the scan button called" each run.

Rects (pixel positions) and state (enabled, visible, populated) are still
queried live — those depend on runtime conditions.

Conventions:
- All driver entry points expect the app to be ALREADY RUNNING under
  `PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1`. Launching is gated and
  stays in /qa-explore.
- Helpers print structured lines to stdout. The LLM reads stdout and
  decides what to probe next. Don't `print()` decorative noise.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import time
from dataclasses import dataclass
from typing import Iterable

from pywinauto import Application
from pywinauto.controls.uiawrapper import UIAWrapper


# ---------------------------------------------------------------------------
# Constants — element names defined by photo-manager's source
# ---------------------------------------------------------------------------

WINDOW_TITLE_RE = r".*Photo Manager.*"

# Top-level menu bar
MENU_FILE = "File"
MENU_ACTION = "Action"
MENU_LIST = "List"
MENU_LOG = "Log"

# File menu items
FILE_SCAN_SOURCES = "Scan Sources…"
FILE_OPEN_MANIFEST = "Open Manifest…"
FILE_SAVE_MANIFEST = "Save Manifest Decisions…"
FILE_EXIT = "Exit"

# Action menu items
ACTION_BY_REGEX = "Set Action by Field/Regex…"
ACTION_EXECUTE = "Execute Action…"

# Scan dialog
SCAN_DIALOG_TITLE = "Scan Sources"
SCAN_BTN_START = "Start Scan"
SCAN_BTN_CLOSE_LOAD = "Close & Load"   # exact UIA accessible name; mirrors scan_dialog.setText("Close && Load")
SCAN_BTN_BROWSE = "Browse…"
SCAN_BTN_REMOVE_ALL = "Remove All"
SCAN_BTN_ADD_SELECTED = "+ Add Selected Folder"
SCAN_AID_LOG = "QApplication.ScanDialog.QPlainTextEdit"
SCAN_AID_OUTPUT_PATH = "QApplication.ScanDialog.QLineEdit"
SCAN_AID_SOURCE_TABLE = (
    "QApplication.ScanDialog.QSplitter._SourceListWidget.QTableWidget"
)

# Execute Action dialog
EXECUTE_DIALOG_TITLE = "Execute Actions — Review"
EXECUTE_BTN = "Execute"
EXECUTE_BTN_SELECT_BY_REGEX = "Select by Field/Regex…"
EXECUTE_CONFIRM_TITLE = "All Files Will Be Deleted"

# Set Action by Field/Regex dialog (inner — opened from Execute dialog)
ACTION_DIALOG_TITLE = "Set Action by Field/Regex"
ACTION_DIALOG_BTN_APPLY = "Apply"
ACTION_DIALOG_BTN_CLOSE = "Close"


# ---------------------------------------------------------------------------
# Win32 plumbing — needed because Qt menu popups are top-level windows but
# don't expose themselves through pywinauto's normal child traversal.
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32
_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)


def list_process_windows(pid: int) -> list[tuple[int, str, str]]:
    """Return [(hwnd, win32_class, title)] for visible top-level windows owned by pid."""
    out: list[tuple[int, str, str]] = []

    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            ppid = ctypes.c_ulong()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(ppid))
            if ppid.value == pid:
                title = ctypes.create_unicode_buffer(256)
                _user32.GetWindowTextW(hwnd, title, 256)
                cls = ctypes.create_unicode_buffer(256)
                _user32.GetClassNameW(hwnd, cls, 256)
                out.append((hwnd, cls.value, title.value))
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def find_popup(pid: int) -> int | None:
    """Find the Qt menu popup window owned by pid (Win32 class contains 'Popup')."""
    for hwnd, cls, _title in list_process_windows(pid):
        if "Popup" in cls:
            return hwnd
    return None


def force_foreground(hwnd: int) -> None:
    _user32.SwitchToThisWindow(hwnd, True)


def _focus(wrapper: UIAWrapper) -> None:
    """Bring `wrapper`'s top-level window to the foreground before a click.

    Pure pywinauto `set_focus()` does the AttachThreadInput dance, which is
    far more reliable than Win32 `SwitchToThisWindow` against Windows'
    foreground-lock heuristic. Falls back to SwitchToThisWindow if set_focus
    raises (e.g. transient menu popups during teardown).
    """
    try:
        wrapper.set_focus()
    except Exception:
        try:
            _user32.SwitchToThisWindow(wrapper.handle, True)
        except Exception:
            pass
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def connect_main(timeout: float = 5) -> tuple[Application, UIAWrapper]:
    app = Application(backend="uia").connect(title_re=WINDOW_TITLE_RE, timeout=timeout)
    return app, app.top_window()


def connect_by_handle(hwnd: int) -> UIAWrapper:
    return Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)


# ---------------------------------------------------------------------------
# Menu navigation
# ---------------------------------------------------------------------------


def open_menu(win: UIAWrapper, menu_title: str) -> UIAWrapper:
    """Click a top-level menu and return the popup wrapper.

    Caller is responsible for clicking an item in the popup; the popup
    closes when an item is clicked or focus moves away.
    """
    _focus(win)
    time.sleep(0.3)
    win.child_window(title=menu_title, control_type="MenuItem").click_input()
    time.sleep(0.5)
    popup_hwnd = find_popup(win.process_id())
    if popup_hwnd is None:
        raise RuntimeError(f"menu popup did not appear for {menu_title!r}")
    return connect_by_handle(popup_hwnd)


def click_menu_item(popup: UIAWrapper, item_title: str) -> None:
    """Click a popup menu item. invoke() raises COMError on these — use click_input."""
    _focus(popup)
    popup.child_window(title=item_title, control_type="MenuItem").click_input()


def menu_path(win: UIAWrapper, menu: str, item: str) -> None:
    """Convenience: open `menu`, click `item`, done."""
    popup = open_menu(win, menu)
    click_menu_item(popup, item)


# ---------------------------------------------------------------------------
# Result-tree reading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupedRow:
    y: int
    cells: tuple[str, ...]   # left-to-right, only non-empty


def read_result_rows(win: UIAWrapper, y_min: int = 600) -> list[GroupedRow]:
    """Walk the main window's TreeView and return rows sorted by screen Y.

    Each row's cells are read by clustering elements with similar y-coords.
    Empty cells (Action column on un-decided files, etc.) are not present.
    """
    items = win.descendants(control_type="TreeItem")
    by_row: dict[int, list[tuple[int, str]]] = {}
    for it in items:
        try:
            txt = (it.window_text() or "").strip()
            r = it.rectangle()
            if not txt or r.top < y_min:
                continue
            key = r.top // 30 * 30   # 30px row height bucket
            by_row.setdefault(key, []).append((r.left, txt))
        except Exception:
            continue
    out: list[GroupedRow] = []
    for y in sorted(by_row):
        cells = tuple(t for _, t in sorted(by_row[y]))
        out.append(GroupedRow(y=y, cells=cells))
    return out


# ---------------------------------------------------------------------------
# Main-window state probes (first-run hint #42, status bar #58, menu #52)
# ---------------------------------------------------------------------------


def read_status_bar_text(win: UIAWrapper) -> str:
    """Return the main window's QStatusBar message, or '' if empty/absent.

    QMainWindow.statusBar().showMessage(text, timeout) shows text for
    timeout ms then clears. Probes immediately after a state transition
    typically still see the message; probes long after see ''.
    """
    try:
        sb = win.child_window(control_type="StatusBar")
        direct = (sb.window_text() or "").strip()
        if direct:
            return direct
        for child in sb.descendants():
            try:
                t = (child.window_text() or "").strip()
                if t:
                    return t
            except Exception:
                continue
    except Exception:
        pass
    return ""


def read_main_window_state(win: UIAWrapper) -> dict:
    """Probe state used by gap-fill checks (#42 first-run, #58 status bar).

    Returns:
        empty_state_visible: True if the "No manifest loaded" hint label
            is in the UIA tree and visible (#42).
        tree_visible: True if the result-tree QTreeView is visible.
        status_bar_text: current QStatusBar message (#58).
    """
    state = {
        "empty_state_visible": False,
        "tree_visible": False,
        "status_bar_text": read_status_bar_text(win),
    }
    for it in win.descendants():
        try:
            t = it.window_text() or ""
            if "No manifest loaded" in t:
                try:
                    state["empty_state_visible"] = bool(it.is_visible())
                except Exception:
                    state["empty_state_visible"] = True
                break
        except Exception:
            continue
    try:
        for tree in win.descendants(control_type="Tree"):
            try:
                if tree.is_visible():
                    state["tree_visible"] = True
                    break
            except Exception:
                continue
    except Exception:
        pass
    return state


def probe_menu_items(win: UIAWrapper, menu_title: str) -> list[tuple[str, bool]]:
    """Open `menu_title`, return [(item_title, enabled)], dismiss popup.

    Used to verify menu enable/disable transitions like #52 ("Remove from
    List" greyed pre-manifest, enabled after manifest loads).
    """
    popup = open_menu(win, menu_title)
    out: list[tuple[str, bool]] = []
    for it in popup.descendants(control_type="MenuItem"):
        try:
            title = (it.window_text() or "").strip()
            if title:
                out.append((title, bool(it.is_enabled())))
        except Exception:
            continue
    # Dismiss popup with Esc — same pattern s01 already uses inline.
    _user32.keybd_event(0x1B, 0, 0, 0)
    _user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(0.2)
    return out


# ---------------------------------------------------------------------------
# Wait helpers
# ---------------------------------------------------------------------------


def wait_for_dialog(pid: int, title: str, timeout: float = 10) -> int:
    """Block until a window with `title` appears in pid; return its hwnd."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd, _cls, t in list_process_windows(pid):
            if t == title:
                return hwnd
        time.sleep(0.2)
    raise TimeoutError(f"dialog {title!r} did not appear within {timeout}s")


def wait_for_text_in(
    edit: UIAWrapper, needles: Iterable[str], timeout: float = 30
) -> str:
    """Poll an Edit/QPlainTextEdit until any of `needles` appears. Returns full text."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            last = edit.window_text() or ""
        except Exception:
            pass
        if any(n in last for n in needles):
            return last
        time.sleep(0.5)
    raise TimeoutError(f"none of {list(needles)!r} appeared in edit within {timeout}s")


# ---------------------------------------------------------------------------
# Composed flows — used by most scenarios
# ---------------------------------------------------------------------------


def open_scan_dialog(win: UIAWrapper) -> tuple[UIAWrapper, int]:
    """Open File > Scan Sources… and return (dialog_wrapper, dialog_hwnd)."""
    pid = win.process_id()
    menu_path(win, MENU_FILE, FILE_SCAN_SOURCES)
    hwnd = wait_for_dialog(pid, SCAN_DIALOG_TITLE, timeout=5)
    return connect_by_handle(hwnd), hwnd


def read_configured_sources(dlg: UIAWrapper) -> list[str]:
    """Return the source paths currently in the Scan dialog's table."""
    out: list[str] = []
    try:
        table = dlg.child_window(
            auto_id=SCAN_AID_SOURCE_TABLE, control_type="Table"
        )
        for cell in table.descendants(control_type="DataItem"):
            t = (cell.window_text() or "").strip()
            if "sandbox" in t:
                out.append(t)
    except Exception:
        pass
    return out


def run_scan_and_wait(
    dlg: UIAWrapper, timeout: float = 60
) -> tuple[str, float]:
    """Click Start Scan, poll log until 'Done.' or error. Returns (full_log, elapsed)."""
    start_btn = dlg.child_window(title=SCAN_BTN_START, control_type="Button")
    log_edit = dlg.child_window(auto_id=SCAN_AID_LOG, control_type="Edit")
    _focus(dlg)
    t0 = time.time()
    start_btn.invoke()
    log = wait_for_text_in(log_edit, ["Done.", "Error", "Failed"], timeout=timeout)
    return log, time.time() - t0


def extract_summary(log: str) -> list[str]:
    """Pull the manifest-summary block from the scan log."""
    out: list[str] = []
    in_summary = False
    for line in log.splitlines():
        if "Migration Manifest Summary" in line or "Group Summary" in line:
            in_summary = True
        if in_summary:
            out.append(line.strip())
        if in_summary and line.strip().startswith("──") and out and len(out) > 1:
            in_summary = False
    return out


def close_and_load_manifest(dlg: UIAWrapper) -> None:
    """Click 'Close & Load' (post-scan dialog button)."""
    btn = dlg.child_window(title=SCAN_BTN_CLOSE_LOAD, control_type="Button")
    _focus(dlg)
    btn.invoke()
    time.sleep(1.0)


def save_manifest_via_native_dialog(
    pid: int, target_path: str, dialog_timeout: float = 10
) -> None:
    """Drive the native QFileDialog opened by File > Save Manifest Decisions….

    1. Locate the filename Edit (ComboBox > Edit, locale-independent).
    2. Set its value via UIA's ValuePattern.SetValue — bypasses keyboard
       (so IMEs like bopomofo can't intercept) and bypasses the
       locale-specific ComboBox label name.
    3. Press Enter to invoke Save.
    4. Wait for the result QMessageBox (success "Save Manifest" or
       failure "Save Manifest Error") and dismiss it with Enter.
       Raises if the result was the error dialog.
    """
    from pywinauto.keyboard import send_keys

    save_hwnd = wait_for_dialog(pid, "Save Manifest Decisions", timeout=dialog_timeout)
    save_dlg = connect_by_handle(save_hwnd)
    _focus(save_dlg)
    time.sleep(0.5)

    # Find the filename Edit: the only Edit nested inside a ComboBox in the
    # native Save dialog. (The other ComboBox is "Save as type:", which has
    # no editable Edit descendant.) Locale-independent.
    filename_edit = None
    for combo in save_dlg.descendants(control_type="ComboBox"):
        try:
            edits = combo.descendants(control_type="Edit")
            if edits:
                filename_edit = edits[0]
                break
        except Exception:
            continue
    if filename_edit is None:
        raise RuntimeError("filename Edit (ComboBox > Edit) not found in Save dialog")

    # Set value via UIA's ValuePattern — bypasses keyboard, focus, and IME.
    # Avoids both IME interception (bopomofo, etc.) and the locale-specific
    # name of the filename ComboBox label.
    filename_edit.iface_value.SetValue(str(target_path))
    time.sleep(0.2)
    send_keys("{ENTER}")

    # Wait for either the success "Save Manifest" QMessageBox or the
    # "Save Manifest Error" critical dialog. Both are dismissed with Enter.
    # Surface the result so a failing scenario can tell which one appeared.
    deadline = time.time() + dialog_timeout
    info_hwnd = None
    matched_title = None
    while time.time() < deadline:
        for hwnd, _cls, t in list_process_windows(pid):
            if t in ("Save Manifest", "Save Manifest Error"):
                info_hwnd = hwnd
                matched_title = t
                break
        if info_hwnd:
            break
        time.sleep(0.2)
    if info_hwnd is None:
        windows = [t for _, _, t in list_process_windows(pid)]
        raise TimeoutError(
            f"neither 'Save Manifest' nor 'Save Manifest Error' appeared "
            f"within {dialog_timeout}s; visible windows={windows!r}"
        )
    print(f"  result_dialog_title={matched_title!r}", flush=True)

    info_dlg = connect_by_handle(info_hwnd)
    if matched_title == "Save Manifest Error":
        for label in info_dlg.descendants(control_type="Text"):
            try:
                txt = (label.window_text() or "").strip()
                if txt:
                    print(f"  error_text: {txt}", flush=True)
            except Exception:
                continue
    _focus(info_dlg)
    time.sleep(0.2)
    send_keys("{ENTER}")
    time.sleep(0.3)
    if matched_title == "Save Manifest Error":
        raise RuntimeError("Save dialog reported an error — see error_text above")


def open_execute_action_dialog(win: UIAWrapper) -> tuple[UIAWrapper, int]:
    """Open Action > Execute Action… and return (dialog_wrapper, dialog_hwnd)."""
    pid = win.process_id()
    menu_path(win, MENU_ACTION, ACTION_EXECUTE)
    hwnd = wait_for_dialog(pid, EXECUTE_DIALOG_TITLE, timeout=5)
    return connect_by_handle(hwnd), hwnd


def mark_all_via_regex(
    execute_dlg: UIAWrapper,
    field: str,
    regex: str,
    action_label: str,
    dialog_timeout: float = 5,
) -> None:
    """Open the inner Set Action by Field/Regex dialog from inside the
    Execute Action dialog, set field+regex+action, click Apply, then Close.

    `field` is the visible text in the Field combo (e.g. "File Name").
    `regex` is set via UIA's ValuePattern to bypass IME (see save-manifest
    helper for the same rationale).
    `action_label` is the visible label in the Set Action combo
    (e.g. "delete" — see SETTABLE_DECISIONS in app/views/constants.py).
    """
    pid = execute_dlg.process_id()
    select_btn = execute_dlg.child_window(
        title=EXECUTE_BTN_SELECT_BY_REGEX, control_type="Button"
    )
    _focus(execute_dlg)
    select_btn.click_input()

    action_hwnd = wait_for_dialog(pid, ACTION_DIALOG_TITLE, timeout=dialog_timeout)
    action_dlg = connect_by_handle(action_hwnd)
    _focus(action_dlg)
    time.sleep(0.3)

    # Two ComboBoxes in this dialog: Field combo (top) and Set Action combo
    # (bottom). Order is deterministic — find them by position (top-most first).
    combos = sorted(
        action_dlg.descendants(control_type="ComboBox"),
        key=lambda c: c.rectangle().top,
    )
    if len(combos) < 2:
        raise RuntimeError(
            f"action dialog: expected >= 2 ComboBoxes, found {len(combos)}"
        )
    field_combo, action_combo = combos[0], combos[1]
    field_combo.select(field)
    time.sleep(0.1)

    # Regex line edit — set via ValuePattern to bypass IME interception.
    edits = action_dlg.descendants(control_type="Edit")
    if not edits:
        raise RuntimeError("action dialog: no Edit control found for regex")
    # Filter out Edits inside ComboBoxes (those belong to the combos, not
    # the standalone QLineEdit).
    standalone_edits = []
    for e in edits:
        try:
            parent = e.parent()
            if parent.element_info.control_type != "ComboBox":
                standalone_edits.append(e)
        except Exception:
            standalone_edits.append(e)
    if not standalone_edits:
        raise RuntimeError("action dialog: no standalone Edit (regex line) found")
    regex_edit = standalone_edits[0]
    regex_edit.iface_value.SetValue(regex)
    time.sleep(0.1)

    action_combo.select(action_label)
    time.sleep(0.1)

    apply_btn = action_dlg.child_window(
        title=ACTION_DIALOG_BTN_APPLY, control_type="Button"
    )
    apply_btn.click_input()
    time.sleep(0.3)

    close_btn = action_dlg.child_window(
        title=ACTION_DIALOG_BTN_CLOSE, control_type="Button"
    )
    close_btn.click_input()
    time.sleep(0.3)


def execute_and_confirm(
    execute_dlg: UIAWrapper, dialog_timeout: float = 10
) -> None:
    """Click Execute on the Execute Action dialog, then Yes on the
    'All Files Will Be Deleted' confirmation QMessageBox.

    Returns when the Execute Action dialog has accepted (closed) — that's
    the signal that send2trash + mark_executed have completed.
    """
    pid = execute_dlg.process_id()
    execute_btn = execute_dlg.child_window(title=EXECUTE_BTN, control_type="Button")
    _focus(execute_dlg)
    execute_btn.click_input()

    confirm_hwnd = wait_for_dialog(pid, EXECUTE_CONFIRM_TITLE, timeout=dialog_timeout)
    confirm_dlg = connect_by_handle(confirm_hwnd)
    _focus(confirm_dlg)
    time.sleep(0.2)
    yes_btn = confirm_dlg.child_window(title="Yes", control_type="Button")
    yes_btn.click_input()
    time.sleep(0.3)

    # Wait for the Execute dialog to close (signals execution completed).
    deadline = time.time() + dialog_timeout
    while time.time() < deadline:
        windows = [t for _, _, t in list_process_windows(pid)]
        if EXECUTE_DIALOG_TITLE not in windows:
            return
        time.sleep(0.2)
    raise TimeoutError(
        f"Execute Action dialog did not close within {dialog_timeout}s after "
        f"confirming the deletion prompt"
    )


def cancel_scan_dialog(dlg: UIAWrapper) -> None:
    """Click the title-bar Close (×) to cancel a scan or close pre-scan."""
    _focus(dlg)
    # Locale-named close button on the title bar
    try:
        for b in dlg.descendants(control_type="Button"):
            r = b.rectangle()
            t = b.window_text() or ""
            if r.top < 320 and r.left > 2400 and t in ("關閉", "Close", "X"):
                b.click_input()
                time.sleep(0.5)
                return
    except Exception:
        pass
