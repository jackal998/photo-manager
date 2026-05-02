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
    force_foreground(win.handle)
    time.sleep(0.3)
    win.child_window(title=menu_title, control_type="MenuItem").click_input()
    time.sleep(0.5)
    popup_hwnd = find_popup(win.process_id())
    if popup_hwnd is None:
        raise RuntimeError(f"menu popup did not appear for {menu_title!r}")
    return connect_by_handle(popup_hwnd)


def click_menu_item(popup: UIAWrapper, item_title: str) -> None:
    """Click a popup menu item. invoke() raises COMError on these — use click_input."""
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
    btn.invoke()
    time.sleep(1.0)


def cancel_scan_dialog(dlg: UIAWrapper) -> None:
    """Click the title-bar Close (×) to cancel a scan or close pre-scan."""
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
