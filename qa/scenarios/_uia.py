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
import re
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable

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
MENU_VIEW = "View"

# View menu items
VIEW_LANGUAGE = "Language"
VIEW_LANG_ENGLISH = "English"
VIEW_LANG_ZH_TW = "繁體中文"
LANGUAGE_CONFIRM_TITLE = "Switch language?"

# Exit-dirty prompt — fired by MainWindow.closeEvent when there are
# unsaved decisions. Title and button accessible names come from
# translations/en.yml under the `exit.*` namespace.
EXIT_CONFIRM_TITLE = "Unsaved Changes"
EXIT_BTN_SAVE_LEAVE = "Save & leave"   # source has "Save && leave"; UIA strips one &
EXIT_BTN_LEAVE = "Leave"
EXIT_BTN_BACK = "Back"

# File menu items
FILE_SCAN_SOURCES = "Scan Sources…"
FILE_OPEN_MANIFEST = "Open Manifest…"
FILE_SAVE_MANIFEST = "Save Manifest Decisions…"
FILE_EXIT = "Exit"

# Log menu items (s18) — exact accessible names from menu_controller.py
LOG_OPEN_LATEST_LOG = "Open Latest Log"
LOG_OPEN_LATEST_DELETE_LOG = "Open Latest Delete Log"
LOG_OPEN_LOG_DIRECTORY = "Open Log Directory"
LOG_OPEN_DELETE_LOG_DIRECTORY = "Open Delete Log Directory"

# Corresponding "Not Found" QMessageBox titles in main_window._open_*_log*
LOG_TITLE_LOG_FILE_NOT_FOUND = "Log File Not Found"
LOG_TITLE_DELETE_LOG_NOT_FOUND = "Delete Log Not Found"
LOG_TITLE_LOG_DIR_NOT_FOUND = "Log Directory Not Found"
LOG_TITLE_DELETE_LOG_DIR_NOT_FOUND = "Delete Log Directory Not Found"

# Action menu items
ACTION_BY_REGEX = "Set Action by Field…"
ACTION_EXECUTE = "Execute Action…"
# #410: sibling of ACTION_EXECUTE that pre-filters the Execute dialog to
# groups containing the currently-selected file rows in the main tree.
ACTION_EXECUTE_SELECTED_ONLY = "Execute Action (only selected)…"

# Scan dialog
SCAN_DIALOG_TITLE = "Scan Sources"
SCAN_BTN_START = "Start Scan"
SCAN_BTN_CLOSE_LOAD = "Close & Load"   # exact UIA accessible name; mirrors scan_dialog.setText("Close && Load")
SCAN_BTN_BROWSE = "Browse…"
SCAN_BTN_REMOVE_ALL = "Remove All"
SCAN_BTN_ADD_SELECTED = "+ Add Selected Folder"
# Paths reflect the two-column ScanDialog layout: outer horizontal QSplitter
# wraps an inner vertical QSplitter on each side. Left inner splitter holds
# the tree group and source list; right inner splitter holds a QWidget
# wrapper (output row + params) on top and the log QPlainTextEdit on the
# bottom.
SCAN_AID_LOG = "QApplication.ScanDialog.QSplitter.QSplitter.QPlainTextEdit"
SCAN_AID_OUTPUT_PATH = (
    "QApplication.ScanDialog.QSplitter.QSplitter.QWidget.QLineEdit"
)
SCAN_AID_SOURCE_TABLE = (
    "QApplication.ScanDialog.QSplitter.QSplitter._SourceListWidget.QTableWidget"
)
SCAN_AID_TREE_PATH_FIELD = (
    "QApplication.ScanDialog.QSplitter.QSplitter.QGroupBox._FolderTreePanel.QLineEdit"
)

# Execute Action dialog
EXECUTE_DIALOG_TITLE = "Execute Actions — Review"
EXECUTE_BTN = "Execute"
EXECUTE_BTN_SELECT_BY_REGEX = "Select by Field/Regex…"
EXECUTE_CONFIRM_TITLE = "All Files Will Be Deleted"

# Set Action by Field dialog (inner — opened from Execute dialog)
ACTION_DIALOG_TITLE = "Set Action by Field"
ACTION_DIALOG_BTN_APPLY = "Apply"
# Close button removed in #391 — dismissal goes via Esc-key
# (Qt routes to reject()) or the title-bar X. Use
# ``close_action_dialog(action_dlg)`` to dismiss.

# LockedRowsConfirmDialog (photo-manager#182) — surfaced whenever an
# action would touch a locked row. The three button labels match the
# en.yml `locked_confirm.btn_*` keys; verdict constants below mirror
# the dialog class's own constants and are passed into the
# ``expect_lock_confirm`` parameter of the helpers that drive the
# regex flow / Execute Action flow.
LOCK_CONFIRM_TITLE = "Locked Rows Affected"
LOCK_CONFIRM_BTN_UNLOCK_APPLY = "Unlock & Apply to All"
LOCK_CONFIRM_BTN_UNLOCKED_ONLY = "Apply to Unlocked Only"
LOCK_CONFIRM_BTN_CANCEL = "Cancel"

# Convenience verdict aliases — point at the button label strings above.
# Scenarios can write ``_uia.LOCK_CONFIRM_APPLY_UNLOCKED_ONLY`` rather
# than the literal button text, which keeps a single source of truth
# for the label drift check in ``test_uia_label_coupling.py``.
LOCK_CONFIRM_APPLY_ALL_UNLOCKED = LOCK_CONFIRM_BTN_UNLOCK_APPLY
LOCK_CONFIRM_APPLY_UNLOCKED_ONLY = LOCK_CONFIRM_BTN_UNLOCKED_ONLY
LOCK_CONFIRM_CANCEL = LOCK_CONFIRM_BTN_CANCEL

# DeleteRegexConfirmDialog (D3 from #350, Wave 10) — surfaced whenever
# ActionDialog's Apply fires with action="delete" and a live preview
# count is available. en.yml `delete_regex_confirm.*` keys; helpers
# below dismiss it (default: confirm) so scenarios that exercise the
# regex+delete flow don't get blocked. Scenarios that want to test
# the cancel path explicitly call ``drive_delete_regex_confirm(pid,
# confirm=False)``.
DELETE_CONFIRM_TITLE = "Confirm bulk-delete decision"
DELETE_CONFIRM_BTN_CANCEL = "Cancel"
# Confirm button label is "Mark {matched} files for deletion" — variable
# N forces a prefix match in the lookup helper rather than an exact
# string. Private (leading underscore) so it's filtered out by the
# test_uia_label_coupling probe: "Mark " on its own is not a user-facing
# label that should be drift-checked against app source.
_DELETE_CONFIRM_BTN_CONFIRM_PREFIX = "Mark "


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


# Default Win32 classes for top-level windows that QA drivers spawn as a
# side effect of os.startfile / explorer.exe shell calls. Closing windows
# of these classes is safe (they're file/folder viewers, not core shell
# infrastructure like Progman or Shell_TrayWnd).
DEFAULT_SHELL_CLASSES: tuple[str, ...] = (
    "CabinetWClass",   # Windows File Explorer folder window
    "Notepad",         # Built-in Notepad (default for .log / .txt)
    "Notepad++",       # Notepad++ if user has it as default
)


def list_top_level_windows(
    classes: tuple[str, ...] | None = None,
) -> list[tuple[int, str, str]]:
    """Return ``[(hwnd, win32_class, title)]`` for visible top-level windows.

    If ``classes`` is given, return only windows whose Win32 class name
    is in the tuple. Use this to snapshot before a click that spawns OS
    shell windows, then diff after to find what appeared.
    """
    out: list[tuple[int, str, str]] = []

    def cb(hwnd, _):
        if _user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls, 256)
            if classes is None or cls.value in classes:
                title = ctypes.create_unicode_buffer(512)
                _user32.GetWindowTextW(hwnd, title, 512)
                out.append((hwnd, cls.value, title.value))
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def list_explorer_windows() -> list[tuple[int, str]]:
    """Return [(hwnd, title)] for every visible top-level Windows Explorer
    window, regardless of process owner.

    Backward-compat wrapper — prefer ``list_top_level_windows`` for new
    callers. Used by s19's title-shape assertion for the Open Folder action.
    """
    return [
        (hwnd, title)
        for hwnd, _cls, title in list_top_level_windows(("CabinetWClass",))
    ]


_WM_CLOSE = 0x0010


def close_window_by_hwnd(hwnd: int) -> None:
    """Politely ask a window to close via PostMessage(WM_CLOSE).

    Does NOT use ``taskkill`` — explorer.exe is the user's shell process
    (manages desktop, taskbar, system tray); killing it would log them
    out. ``WM_CLOSE`` only closes the targeted folder window.
    """
    _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)


def close_new_shell_windows(
    baseline: list[tuple[int, str, str]],
    classes: tuple[str, ...] | None = None,
) -> list[tuple[int, str, str]]:
    """Snapshot-diff helper: close any shell windows that appeared since
    ``baseline`` was captured.

    Designed for drivers that intentionally spawn Notepad / Explorer /
    similar viewers as a side effect of the action under test (s18 Log
    menu, s19 Open Folder). Workflow::

        baseline = list_top_level_windows(DEFAULT_SHELL_CLASSES)
        # … perform the click that spawns the shell window …
        time.sleep(1)
        closed = close_new_shell_windows(baseline)

    Args:
        baseline: List of ``(hwnd, class, title)`` taken before the action.
            Only ``hwnd`` is read; class/title are ignored, kept for
            symmetry with the producer.
        classes: Class allowlist for the *current* snapshot. Defaults to
            ``DEFAULT_SHELL_CLASSES``. Pass a narrower tuple to scope
            cleanup (e.g. ``("CabinetWClass",)`` to close only Explorer
            spawns, leaving Notepad alone).

    Returns:
        ``[(hwnd, class, title)]`` for every window that was sent
        WM_CLOSE — useful for logging "what we cleaned up".

    Note: only windows in ``classes`` are considered. If the user's
    default app for .log / .csv files is something other than Notepad
    or Notepad++ (e.g. VSCode, Sublime), those windows are NOT
    auto-closed. The driver should document that residual.
    """
    if classes is None:
        classes = DEFAULT_SHELL_CLASSES
    baseline_hwnds = {h for h, _, _ in baseline}
    new = [
        (h, c, t)
        for h, c, t in list_top_level_windows(classes)
        if h not in baseline_hwnds
    ]
    for hwnd, _cls, _title in new:
        close_window_by_hwnd(hwnd)
    return new


def find_popup(pid: int) -> int | None:
    """Find the Qt menu popup window owned by pid (Win32 class contains 'Popup')."""
    for hwnd, cls, _title in list_process_windows(pid):
        if "Popup" in cls:
            return hwnd
    return None


def force_foreground(hwnd: int) -> None:
    _user32.SwitchToThisWindow(hwnd, True)


def _focus(wrapper: UIAWrapper, timeout: float = 1.5) -> None:
    """Bring `wrapper`'s top-level window to the foreground, then *wait
    until Windows actually honours it* before returning.

    Why this matters: Windows enforces a foreground-lock heuristic — a
    background process can call `SetForegroundWindow` and have it
    silently no-op when another window owns foreground (e.g. the
    terminal that launched this driver). The previous "fire and sleep
    50 ms" version returned before the foreground change took effect,
    so the next `click_input` was delivered to whatever window WAS
    foreground (terminal/IDE) and the photo-manager click was lost.

    This version retries `set_focus()` inside a poll loop until
    `GetForegroundWindow()` matches the target HWND, then returns.
    Falls through silently after `timeout` so the caller can still
    attempt the click — the action layer (open_menu, etc.) has its
    own retry to recover from the rare case where foreground refuses.
    """
    target = wrapper.handle
    if _user32.GetForegroundWindow() == target:
        return  # already foreground — don't perturb state

    deadline = time.time() + timeout
    last_set_focus_at = 0.0
    while time.time() < deadline:
        if _user32.GetForegroundWindow() == target:
            return
        # Retry set_focus at most every 200 ms — calling it every 50 ms
        # piles up AttachThreadInput pairs faster than Windows can drain
        # them, which thrashes focus state and can dismiss popups or
        # delay the next click. 200 ms gives each call a fair chance.
        if time.time() - last_set_focus_at > 0.2:
            try:
                wrapper.set_focus()
            except Exception:
                try:
                    _user32.SwitchToThisWindow(target, True)
                except Exception:
                    pass
            last_set_focus_at = time.time()
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def connect_main(timeout: float = 20) -> tuple[Application, UIAWrapper]:
    # 20s default absorbs GitHub Actions Windows-runner cold-start variance;
    # local runs finish in <2s and aren't affected by the higher ceiling.
    app = Application(backend="uia").connect(title_re=WINDOW_TITLE_RE, timeout=timeout)
    return app, app.top_window()


def connect_by_handle(hwnd: int) -> UIAWrapper:
    return Application(backend="uia").connect(handle=hwnd).window(handle=hwnd)


# ---------------------------------------------------------------------------
# Menu navigation
# ---------------------------------------------------------------------------


def open_menu(win: UIAWrapper, menu_title: str) -> UIAWrapper:
    """Click a top-level menu and return the popup wrapper.

    Retries up to 3 times because the foreground-lock heuristic on
    Windows can swallow the first click_input if another window
    (terminal, IDE) was foreground when the helper was called. Between
    retries, send Esc to clear any stuck menu-bar-active state from a
    swallowed click.

    Caller is responsible for clicking an item in the popup; the popup
    closes when an item is clicked or focus moves away.
    """
    pid = win.process_id()
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            _focus(win)
            time.sleep(0.3 + attempt * 0.2)
            win.child_window(
                title=menu_title, control_type="MenuItem"
            ).click_input()
        except Exception as exc:
            last_err = exc
            time.sleep(0.3)
            continue
        time.sleep(0.5)
        popup_hwnd = find_popup(pid)
        if popup_hwnd is not None:
            return connect_by_handle(popup_hwnd)
        # No popup — bar may be in active mode from a swallowed click.
        # Send Esc to reset before retrying.
        _user32.keybd_event(0x1B, 0, 0, 0)
        _user32.keybd_event(0x1B, 0, 2, 0)
        time.sleep(0.2)
    raise RuntimeError(
        f"menu popup did not appear for {menu_title!r} after 3 attempts"
        + (f" (last click error: {last_err!r})" if last_err else "")
    )


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


# Basename regex used by read_tree_row_order to pick the file-name cell out
# of each tree row. Listed extensions are the formats photo-manager scans
# (see ``scanner/media_extract.py``). Case-insensitive; no leading dot.
_BASENAME_RE = re.compile(
    r"^[^/\\]+\.(jpg|jpeg|png|gif|bmp|tif|tiff|heic|heif|webp|"
    r"raw|cr2|nef|arw|dng|orf|rw2|mp4|mov|m4v|avi|mkv)$",
    re.IGNORECASE,
)


def read_tree_row_order(win: UIAWrapper) -> list[str]:
    """Return basenames of file rows in display (top-to-bottom) order.

    Unlike ``read_result_rows``, this helper does NOT y-filter — the
    windows-latest CI runner renders the main window smaller, every
    TreeItem's ``top < 600``, and ``read_result_rows`` silently returns
    ``[]`` on CI.

    Approach: walk raw ``TreeItem`` descendants, keep only cells whose
    text matches :data:`_BASENAME_RE` (the File Name column for file
    rows — uniquely identifying), and return them sorted by ``top``.
    No Y-bucketing — earlier versions used a 30-px bucket which on the
    CI render (rows ~15-16 px tall, smaller font / DPI than a dev
    workstation) merged adjacent rows pairwise and dropped every
    other file. Sorting basename-cells directly avoids the bucket
    boundary entirely; each file row has exactly one File Name cell,
    so there's nothing to cluster.

    Group-header rows (which don't contain a basename) are naturally
    excluded by the regex filter — the returned list is file rows
    only, in the order Qt is currently displaying them. This is the
    right oracle for sort-state assertions: it's what the user sees,
    not what's in the database.
    """
    items = win.descendants(control_type="TreeItem")
    name_cells: list[tuple[int, str]] = []
    for it in items:
        try:
            txt = (it.window_text() or "").strip()
            if not txt or not _BASENAME_RE.match(txt):
                continue
            r = it.rectangle()
            name_cells.append((r.top, txt))
        except Exception:
            continue
    name_cells.sort(key=lambda pair: pair[0])
    return [t for _, t in name_cells]


def read_selected_tree_row_basenames(win: UIAWrapper) -> list[str]:
    """Return basenames of currently-selected file rows in the result tree.

    Counterpart to :func:`read_tree_row_order` for selection state.
    Walks ``TreeItem`` descendants, filters by basename regex (file rows
    only — group-header rows aren't selectable file targets), and keeps
    only those whose UIA SelectionItem pattern reports selected.

    Used by #243-style probes that inspect post-action UI state (e.g.
    s49 verifying "Auto select after scan" actually highlights the
    keeper row, not just writes ``action=KEEP`` to the manifest).
    Returned order matches display order (sorted by row top).
    """
    items = win.descendants(control_type="TreeItem")
    selected: list[tuple[int, str]] = []
    for it in items:
        try:
            txt = (it.window_text() or "").strip()
            if not txt or not _BASENAME_RE.match(txt):
                continue
            if not it.is_selected():
                continue
            r = it.rectangle()
            selected.append((r.top, txt))
        except Exception:
            # is_selected() raises on TreeItems without a SelectionItem
            # pattern (group-header rows on some Qt builds). Skip them
            # rather than crashing the probe.
            continue
    selected.sort(key=lambda pair: pair[0])
    return [t for _, t in selected]


def click_column_header(win: UIAWrapper, header_text: str) -> None:
    """Click the result-tree column header whose label equals ``header_text``.

    PySide6's QTreeView exposes each section as its own top-level
    ``Header`` control (the same shape s47 relies on for width probes
    — see ``s47_column_layout_persist._column_width``). We find the
    section by visible ``window_text`` and click its centre via
    ``click_input``. ``invoke()`` doesn't work on header sections —
    they're not invokable patterns; the click reaches Qt through
    ``QHeaderView.mousePressEvent`` which is what fires
    ``sectionClicked`` and ultimately ``MainWindow._on_header_clicked``.

    Raises ``RuntimeError`` with the full list of visible header
    labels when no match is found, mirroring s47's diagnostic so
    label drift surfaces immediately on CI rather than as a confusing
    "click did nothing" symptom.
    """
    for h in win.descendants(control_type="Header"):
        try:
            if not h.is_visible():
                continue
            if (h.window_text() or "").strip() == header_text:
                h.click_input()
                return
        except Exception:
            continue
    seen: list[str] = []
    for h in win.descendants(control_type="Header"):
        try:
            seen.append((h.window_text() or "").strip())
        except Exception:
            seen.append("<err>")
    raise RuntimeError(
        f"Header section {header_text!r} not found; saw: {seen!r}"
    )


def read_column_headers(win: UIAWrapper) -> list[str]:
    """Return the visible tree column-header labels in display order.

    Walks ``win.descendants(control_type="Header")`` (same approach
    :func:`click_column_header` uses), filters to visible sections,
    and orders by the section's screen left coordinate so the result
    matches what the user sees left-to-right.

    Used by ``qa/probes/field_dropdown_inventory.py`` (#243) to diff
    the result-tree header set against the Select dialog's field
    dropdown — see #238 for the bug class this catches. Empty header
    text is skipped (the leftmost decorative section on some Qt
    builds reports as a blank Header).
    """
    pairs: list[tuple[int, str]] = []
    for h in win.descendants(control_type="Header"):
        try:
            if not h.is_visible():
                continue
            text = (h.window_text() or "").strip()
            if not text:
                continue
            pairs.append((h.rectangle().left, text))
        except Exception:
            continue
    pairs.sort(key=lambda p: p[0])
    return [t for _, t in pairs]


def read_combobox_items(combo: UIAWrapper) -> list[str]:
    """Return all item texts of a QComboBox in dropdown order.

    Expands the combo, walks the popup ``List`` widget's ``ListItem``
    descendants for visible items, then sends End-key + Home-key
    through Win32 ``keybd_event`` so Qt scrolls the popup and any items
    past ``maxVisibleItems`` (default 10) come into view too. Re-walks
    descendants on each scroll, deduping by text. Collapses the combo
    before returning so the dialog is left in its previous state.

    pywinauto's ``ComboBoxWrapper.texts()`` looks ideal at first glance
    but Qt's QComboBox doesn't expose ItemContainerPattern through UIA
    (verified empirically — ``iface_item_container`` raises
    ``NoPatternInterfaceError``) and the fallback iterates the combo's
    direct children, which on Qt6 is just an empty ``List`` wrapper.
    The s50 commit message documents the visibility/scroll behaviour
    this helper has to work around to enumerate the 11th item
    ("Resolution") on the Set Action Field dropdown — see #238.

    Order is the first-discovery order across scroll positions, which
    matches the popup's display order (top to bottom) under End-only
    scrolling. Callers that need set-based diffs (the typical inventory
    probe use case) can convert to a ``set`` directly.

    Empty strings are filtered out. Whitespace is preserved otherwise
    so diff probes catch accidental label trimming.
    """
    def _collect(seen: set[str], out: list[str]) -> None:
        for d in combo.descendants(control_type="ListItem"):
            try:
                text = (d.window_text() or "").strip()
            except Exception:
                continue
            if text and text not in seen:
                seen.add(text)
                out.append(text)

    seen: set[str] = set()
    out: list[str] = []

    expanded = False
    try:
        try:
            combo.expand()
            expanded = True
        except Exception:
            # ExpandCollapsePattern unavailable — fall back to reading
            # whatever's already there. Won't find any items on Qt6 but
            # gives the caller a clean empty result rather than a crash.
            pass
        time.sleep(0.25)
        _collect(seen, out)

        # End-key scrolls Qt's QComboBoxListView to the last item — the
        # items beyond ``maxVisibleItems`` now render as descendants.
        # Home-key brings the view back so the dialog's visual state is
        # restored before collapse.
        _user32.keybd_event(0x23, 0, 0, 0)   # VK_END down
        _user32.keybd_event(0x23, 0, 2, 0)   # VK_END up
        time.sleep(0.2)
        _collect(seen, out)
        _user32.keybd_event(0x24, 0, 0, 0)   # VK_HOME down
        _user32.keybd_event(0x24, 0, 2, 0)   # VK_HOME up
        time.sleep(0.1)
    finally:
        if expanded:
            try:
                combo.collapse()
            except Exception:
                pass

    return out


# ---------------------------------------------------------------------------
# Set Action by Field dialog — open / close (no form drive)
# ---------------------------------------------------------------------------


def open_action_by_regex_dialog(
    win: UIAWrapper, dialog_timeout: float = 5
) -> tuple[UIAWrapper, int]:
    """Open the Set Action by Field dialog from the menu bar.

    Counterpart to :func:`mark_all_via_regex_standalone` for probes that
    want to inspect the dialog *without* filling and applying the form.
    Returns ``(action_dlg, hwnd)``. Caller owns the dialog and must
    close it via :func:`close_action_dialog` when done.

    Used by ``qa/probes/field_dropdown_inventory.py`` (#243).
    """
    pid = win.process_id()
    menu_path(win, MENU_ACTION, ACTION_BY_REGEX)
    hwnd = wait_for_dialog(pid, ACTION_DIALOG_TITLE, timeout=dialog_timeout)
    dlg = connect_by_handle(hwnd)
    _focus(dlg)
    time.sleep(0.2)
    return dlg, hwnd


def close_action_dialog(action_dlg: UIAWrapper) -> None:
    """Dismiss the Set-Action-by-Field/Regex dialog via Esc-key.

    #391 dropped the explicit Close button — dismissal goes through
    the OS-level paths (Esc routes to ``QDialog.reject()`` by default
    Qt behaviour; title-bar X fires the same path). Esc-key is the
    simpler and more reliable of the two from pywinauto's UIA layer.

    Counterpart to :func:`open_action_by_regex_dialog` — leaves the
    main window focused so subsequent probes can continue.
    """
    _focus(action_dlg)
    action_dlg.type_keys("{ESC}")
    time.sleep(0.3)


def read_preview_items(action_dlg: UIAWrapper) -> list[str]:
    """Return the visible row texts of the ``.regexPreviewList`` ListView.

    Used by Wave 11 probes (#361) to verify what the preview pane
    actually renders — distinct from the live-preview counter which
    only carries the match count. The list is a QListWidget whose
    ListItem descendants carry the displayed strings (basename for
    File Name fields, folder path for Folder, "Group N — basename
    (value)" rows for Top-N). Returns an empty list if the list
    isn't found or no items are present.
    """
    lst = _find_descendant_by_aid_suffix(action_dlg, "List", ".regexPreviewList")
    if lst is None:
        return []
    items: list[str] = []
    for it in lst.descendants(control_type="ListItem"):
        try:
            text = (it.window_text() or "").strip()
        except Exception:
            text = ""
        if text:
            items.append(text)
    return items


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
    # Dismiss popup with Esc TWICE — Qt's QMenuBar leaves the bar in
    # "active mode" after a single Esc (popup closed, but the bar still
    # has focus and swallows the next menu-bar click as an exit-active
    # signal instead of opening a new popup). The second Esc exits
    # active mode so subsequent open_menu calls behave normally.
    _user32.keybd_event(0x1B, 0, 0, 0)
    _user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(0.1)
    _user32.keybd_event(0x1B, 0, 0, 0)
    _user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(0.2)
    return out


# ---------------------------------------------------------------------------
# Wait helpers
# ---------------------------------------------------------------------------


def assert_no_dialog_within(
    pid: int, title: str, seconds: float = 1.0
) -> bool:
    """Inverse of wait_for_dialog: True iff no window matching ``title``
    appears in ``pid`` within the polling window. Useful when a click
    *might* spawn an unwanted dialog (e.g. an unexpected Not-Found
    QMessageBox from a Log menu happy path)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        for _hwnd, _cls, t in list_process_windows(pid):
            if t == title:
                return False
        time.sleep(0.1)
    return True


def assert_no_qt_popup_within(
    pid: int, seconds: float = 1.0,
    baseline: list[int] | None = None,
) -> bool:
    """True iff no NEW Qt popup-class window appears in ``pid`` within
    the polling window.

    Used by s25 to verify that right-clicks on empty tree areas / menu-
    bar / unselected rows do NOT spawn a Qt context menu. Distinct from
    ``assert_no_dialog_within`` (matches by title) — Qt popup windows
    have empty titles and are matched by Win32 class name (something
    containing ``"Popup"`` — typically
    ``Qt<ver>QWindowPopupDropShadowSaveBits``).

    ``baseline`` lets the caller exclude popups that already existed
    when the probe started (e.g. a tooltip lingering from a prior
    hover). Only popups whose hwnd is NOT in ``baseline`` count as
    "spawned" for this check.
    """
    base = set(baseline or [])
    deadline = time.time() + seconds
    while time.time() < deadline:
        for hwnd, cls, _ in list_process_windows(pid):
            if "Popup" in cls and hwnd not in base:
                return False
        time.sleep(0.1)
    return True


def dismiss_dialog_by_title(pid: int, title: str, timeout: float = 3) -> bool:
    """Find a window with ``title`` in ``pid`` and dismiss it via Esc.

    Returns True if the dialog was found and dismissed, False if it
    never appeared. Used to clear a Not-Found QMessageBox after the
    layer-3 driver has verified its title.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd, _cls, t in list_process_windows(pid):
            if t == title:
                dlg = connect_by_handle(hwnd)
                _focus(dlg)
                _user32.keybd_event(0x1B, 0, 0, 0)        # Esc down
                _user32.keybd_event(0x1B, 0, 2, 0)        # Esc up
                time.sleep(0.3)
                return True
        time.sleep(0.1)
    return False


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
    """Open File > Scan Sources… and return (dialog_wrapper, dialog_hwnd).

    #424 — bumped 5s → 10s to absorb the dialog's slightly heavier
    construction now that ScanDialog has the stage / throughput / ETA
    progress frame (QFrame + QProgressBar + 2 QLabels + 3-pane splitter
    re-layout). On a warm local machine the dialog still appears in
    <1s; the bump only matters for CI cold-launch on Windows runners
    where the 5s window was a tight fit even before #424.
    Other dialog waiters in this module already use 10s — this aligns.
    """
    pid = win.process_id()
    menu_path(win, MENU_FILE, FILE_SCAN_SOURCES)
    hwnd = wait_for_dialog(pid, SCAN_DIALOG_TITLE, timeout=10)
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


# ---------------------------------------------------------------------------
# ScanDialog source-list widget operations (s17) — drive _SourceListWidget
# directly: add via tree-panel path field, reorder via ↑↓, toggle Recursive,
# remove via ×. Y-coordinate sort makes per-row dispatch unambiguous because
# the only QCheckBox / ↑ / ↓ / × controls in the dialog are the per-row ones.
# ---------------------------------------------------------------------------


def _find_tree_path_field(dlg: UIAWrapper) -> UIAWrapper:
    """Return the _FolderTreePanel QLineEdit (where the user types a path).

    Tries the auto_id constant first; falls back to "the QLineEdit whose
    placeholder mentions 'absolute folder path'" so we survive auto_id
    hierarchy drift if Qt re-parents the widget.
    """
    try:
        edit = dlg.child_window(
            auto_id=SCAN_AID_TREE_PATH_FIELD, control_type="Edit"
        )
        edit.element_info  # force resolution
        return edit
    except Exception:
        pass
    # Fallback — placeholder text comes from photo-manager source, locale-stable.
    for edit in dlg.descendants(control_type="Edit"):
        try:
            help_text = (edit.legacy_properties().get("Help") or "").lower()
            if "absolute folder path" in help_text:
                return edit
        except Exception:
            continue
    raise RuntimeError("tree-panel path field not found in ScanDialog")


def add_source_via_path_field(dlg: UIAWrapper, path: str) -> None:
    """Add a source folder via the tree-panel path field + ``+ Add`` button.

    Sets the path text via UIA ValuePattern (IME-safe, focus-independent),
    then clicks the adjacent ``+ Add`` QPushButton whose ``clicked`` signal
    runs ``_on_add_typed``. We deliberately do NOT press Enter here:
    ``Start Scan`` is the dialog's default button (``setDefault(True)``),
    so any Enter keystroke that reaches the dialog instead of the focused
    QLineEdit kicks off a scan with whatever's already in the source list.
    Windows foreground-lock and IME races make that focus delivery
    intermittent — the result was a stray scan running in the background,
    holding the run-manifest.sqlite handle, and producing a ``Scan Failed``
    QMessageBox later when the next scan tried to overwrite the file.
    Clicking the dedicated button has no such race surface.
    """
    edit = _find_tree_path_field(dlg)
    _focus(dlg)
    edit.iface_value.SetValue(str(path))
    time.sleep(0.1)

    add_btn = next(
        (
            b
            for b in dlg.descendants(control_type="Button")
            if (b.window_text() or "").strip() == "+ Add"
        ),
        None,
    )
    if add_btn is None:
        raise RuntimeError("'+ Add' button not found in ScanDialog")
    try:
        add_btn.invoke()
    except Exception:
        add_btn.click_input()
    time.sleep(0.3)


def _table_cells_by_row(dlg: UIAWrapper) -> list[list[tuple[int, int, int, int]]]:
    """Return per-row cell rectangles, row-sorted top-to-bottom and
    column-sorted left-to-right within each row.

    Each cell is ``(left, top, right, bottom)`` in screen coordinates.
    Used to locate row controls (Recursive checkbox, ↑/↓ pair, ×) by
    pixel position — Qt's setCellWidget'd children are NOT exposed in
    the UIA tree, so DataItem rectangles are the only reliable hook.
    """
    try:
        table = dlg.child_window(
            auto_id=SCAN_AID_SOURCE_TABLE, control_type="Table"
        )
    except Exception:
        return []
    raw: list[tuple[int, int, int, int]] = []
    for it in table.descendants(control_type="DataItem"):
        try:
            r = it.rectangle()
            raw.append((r.left, r.top, r.right, r.bottom))
        except Exception:
            continue
    if not raw:
        return []

    raw.sort(key=lambda c: (c[1], c[0]))
    rows: list[list[tuple[int, int, int, int]]] = []
    cur: list[tuple[int, int, int, int]] = []
    last_y = -10**9
    for left, top, right, bottom in raw:
        if abs(top - last_y) > 10:
            if cur:
                rows.append(sorted(cur, key=lambda c: c[0]))
            cur = [(left, top, right, bottom)]
            last_y = top
        else:
            cur.append((left, top, right, bottom))
    if cur:
        rows.append(sorted(cur, key=lambda c: c[0]))
    return rows


def read_source_paths(dlg: UIAWrapper) -> list[str]:
    """Return source-table paths in row order (top-to-bottom).

    Reads only column 0 (path text), which is a real QTableWidgetItem
    and so is exposed in the UIA tree. The Recursive / × cells are
    setCellWidget'd and have no accessible state — callers cannot read
    those, only act on them.

    The table is displayed sorted by path (case-insensitive) regardless
    of insertion order — see _SourceListWidget._rebuild_table.
    """
    try:
        table = dlg.child_window(
            auto_id=SCAN_AID_SOURCE_TABLE, control_type="Table"
        )
    except Exception:
        return []
    candidates: list[tuple[int, str]] = []
    for cell in table.descendants(control_type="DataItem"):
        try:
            txt = (cell.window_text() or "").strip()
            if not txt or ("\\" not in txt and "/" not in txt):
                continue
            candidates.append((cell.rectangle().top, txt))
        except Exception:
            continue
    candidates.sort(key=lambda c: c[0])
    return [t for _, t in candidates]


def click_source_row_button(dlg: UIAWrapper, row: int, kind: str) -> None:
    """Click × on the given 0-indexed row by pixel coordinates.

    ``kind`` must be ``"remove"`` — the ↑/↓ reorder buttons were
    removed in #213 (the folder list is now sorted by path and scan
    order doesn't affect dedup outcome).

    setCellWidget'd buttons are not exposed in Qt's UIA tree, so we
    target the × button by clicking the center of the column-2
    DataItem rectangle.

    After the click the table is rebuilt from scratch (see
    _SourceListWidget._remove); callers should re-read row state via
    read_source_paths.
    """
    import pywinauto.mouse

    if kind != "remove":
        raise ValueError(
            f"kind must be 'remove' (↑/↓ buttons were removed in #213); "
            f"got {kind!r}"
        )

    rows = _table_cells_by_row(dlg)
    if row < 0 or row >= len(rows):
        raise IndexError(f"row {row} out of range (have {len(rows)} rows)")
    cells = rows[row]
    if len(cells) < 3:
        raise RuntimeError(
            f"row {row} has only {len(cells)} cells; expected 3 "
            f"(Path, Recursive, ×)"
        )

    left, top, right, bottom = cells[2]
    cy = top + (bottom - top) // 2
    cx = left + (right - left) // 2

    _focus(dlg)
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    time.sleep(0.3)


def toggle_source_row_recursive(dlg: UIAWrapper, row: int) -> None:
    """Toggle the Recursive checkbox in the given 0-indexed row.

    setCellWidget'd checkboxes do not surface in the UIA tree, so we
    click the center of the column-1 cell rectangle. Qt routes the
    click to the cell widget (centered checkbox) which fires
    stateChanged → _on_recursive_changed.

    There is no UIA-level way to read the resulting toggle state back;
    callers should treat this as fire-and-forget and verify the
    behavioral effect through other channels (e.g. a subsequent scan
    using non-recursive depth produces a different file count).
    """
    import pywinauto.mouse

    rows = _table_cells_by_row(dlg)
    if row < 0 or row >= len(rows):
        raise IndexError(f"row {row} out of range (have {len(rows)} rows)")
    cells = rows[row]
    if len(cells) < 2:
        raise RuntimeError(
            f"row {row} has only {len(cells)} cells; expected at least 2"
        )
    left, top, right, bottom = cells[1]
    cx = left + (right - left) // 2
    cy = top + (bottom - top) // 2
    _focus(dlg)
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    time.sleep(0.2)


def click_remove_all_sources(dlg: UIAWrapper) -> None:
    """Click the 'Remove All' header button to empty the source list."""
    btn = dlg.child_window(title=SCAN_BTN_REMOVE_ALL, control_type="Button")
    _focus(dlg)
    btn.click_input()
    time.sleep(0.3)


def _find_dialog_button(dlg: UIAWrapper, title: str) -> UIAWrapper:
    """Find a Button by ``title`` inside ``dlg``, picking the bottom-most match.

    Disambiguates against locale-specific title-bar buttons that share the
    same accessible name as the dialog's form-row buttons. Concrete case:
    on a zh-TW Windows session the title-bar Close button reads "關閉" so a
    plain ``dlg.child_window(title="Close", control_type="Button")`` resolves
    cleanly to the Apply/Close form button. On en-US (e.g. GitHub's
    ``windows-latest`` runners) the title-bar Close button is also "Close",
    creating an ambiguous match that pywinauto's ``__resolve_control``
    times out on rather than picking either.

    The form-row buttons sit at the bottom of the dialog rect; the title-bar
    button sits at the top. Sort by ``rectangle().top`` and take the
    bottom-most. When only one Button matches the title (the zh-TW case),
    "bottom-most" reduces to "the one button" — local behavior unchanged.

    Raises ``RuntimeError`` when no Button matches the title.
    """
    candidates: list[tuple[int, UIAWrapper]] = []
    for btn in dlg.descendants(control_type="Button"):
        try:
            if (btn.window_text() or "").strip() == title:
                candidates.append((btn.rectangle().top, btn))
        except Exception:
            continue
    if not candidates:
        raise RuntimeError(f"no Button with title {title!r} found in dialog")
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]


def _find_native_dialog_action_button(native_dlg: UIAWrapper) -> UIAWrapper:
    """Locate the action button (Save/Open/OK) of a Save dialog.

    Handles two tree shapes:

    * **Windows native Common Item Dialog**: bottom row contains the
      action button + Cancel, sometimes plus a navigation-pane toggle
      ("Hide Folders" / "Browse Folders") on the far left. Layout
      (right-to-left): Cancel (rightmost), action button, [optional]
      toggle — so the action button is **2nd-from-rightmost**.

    * **Qt non-native QFileDialog** (``AA_DontUseNativeDialogs``, see
      #129): buttons live inside a ``QDialogButtonBox`` and may be
      laid out **vertically** (Save above Cancel) or horizontally.
      Qt orders the AcceptRole button first, so the topmost-leftmost
      ``QPushButton`` inside the buttonBox is the action button —
      locale-independent for either orientation.

    Locale-independent: identifies by structure, not visible text.

    Why not press Enter via ``send_keys``? On the GitHub-hosted
    ``windows-latest`` runner foreground semantics differ from a real
    desktop session; ``send_keys`` delivers globally to whatever is
    foreground and intermittently misses the dialog. ``click_input``
    on a structurally-located button is locale-independent and doesn't
    rely on foreground staying glued to the dialog.

    Raises ``RuntimeError`` with a full descendant tree dump (written
    to a temp file to bypass console truncation) when neither pattern
    yields the action button.
    """
    # Qt branch: look for QDialogButtonBox (a control_type="Group" with
    # class_name="QDialogButtonBox"). If present, the AcceptRole button
    # is the first QPushButton inside it — topmost in vertical layout,
    # leftmost in horizontal.
    for grp in native_dlg.descendants(control_type="Group"):
        try:
            if (grp.element_info.class_name or "") != "QDialogButtonBox":
                continue
        except Exception:
            continue
        qt_buttons: list[tuple[int, int, UIAWrapper]] = []
        for b in grp.descendants(control_type="Button"):
            try:
                if (b.element_info.class_name or "") != "QPushButton":
                    continue
                r = b.rectangle()
                qt_buttons.append((r.top, r.left, b))
            except Exception:
                continue
        if qt_buttons:
            qt_buttons.sort(key=lambda c: (c[0], c[1]))
            return qt_buttons[0][2]

    # Native Common Item Dialog: 2nd-from-rightmost button in bottom 80px.
    dlg_rect = native_dlg.rectangle()
    candidates: list[tuple[int, int, UIAWrapper]] = []
    for b in native_dlg.descendants(control_type="Button"):
        try:
            r = b.rectangle()
            if r.top >= dlg_rect.bottom - 80:
                candidates.append((r.left, r.top, b))
        except Exception:
            continue
    if len(candidates) < 2:
        # Dump the FULL descendant tree to a file (stdout truncates on
        # Chinese chars in the qa-batch subprocess pipeline). Caller can
        # cat /tmp/qa_dialog_tree.txt to triage.
        import tempfile
        dump_lines: list[str] = []
        dump_lines.append(f"dlg_rect={dlg_rect!r} dlg_class={native_dlg.element_info.class_name!r}")
        try:
            for d in native_dlg.descendants():
                try:
                    r = d.rectangle()
                    ct = d.element_info.control_type
                    t = (d.window_text() or "").strip()
                    aid = d.element_info.automation_id or ""
                    cls = d.element_info.class_name or ""
                    dump_lines.append(
                        f"{ct} class={cls!r} title={t!r} aid={aid!r} "
                        f"rect=({r.left},{r.top},{r.right},{r.bottom})"
                    )
                except Exception as ex:
                    dump_lines.append(f"<descendant-error: {ex!r}>")
                    continue
        except Exception as ex:
            dump_lines.append(f"<top-level-error: {ex!r}>")
        dump_path = Path(tempfile.gettempdir()) / "qa_dialog_tree.txt"
        try:
            dump_path.write_text("\n".join(dump_lines), encoding="utf-8")
        except Exception:
            pass
        raise RuntimeError(
            f"native dialog: expected >= 2 bottom-row buttons "
            f"(action + Cancel), got {len(candidates)}; "
            f"dlg_rect={dlg_rect!r}; full tree dumped to {dump_path}"
        )
    # Sort descending by ``left`` (rightmost first). Cancel is the
    # rightmost in every Common Item Dialog variant; the action button
    # is immediately to its left, regardless of whether the optional
    # navigation toggle is also present further to the left.
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[1][2]


def _find_filename_edit(native_dlg: UIAWrapper) -> UIAWrapper:
    """Find the filename Edit in a Save dialog — native or Qt non-native.

    Two tree shapes are possible:

    * **Windows native IFileSaveDialog**: filename is the only Edit nested
      inside a ComboBox descendant. The native dialog has two ComboBoxes —
      filename (editable) and "Save as type:" (no Edit). Picking by structure
      makes the lookup locale-independent.

    * **Qt non-native QFileDialog** (when ``AA_DontUseNativeDialogs`` is set,
      e.g. via ``PHOTO_MANAGER_QT_FILE_DIALOG=1`` for hosted CI runners that
      can't drive the native COM modal — see #129): filename is a standalone
      ``QLineEdit`` not nested in any ComboBox. The Look-in / file-type
      ComboBoxes are siblings.

    Strategy: try the native pattern first; if no ComboBox > Edit found,
    fall back to "the only Edit not nested in a ComboBox" — that's Qt's
    fileNameEdit. If neither yields a candidate, dump the tree for triage.
    """
    # Native pattern: ComboBox > Edit
    for combo in native_dlg.descendants(control_type="ComboBox"):
        try:
            edits = combo.descendants(control_type="Edit")
            if edits:
                return edits[0]
        except Exception:
            continue

    # Qt fallback: standalone Edit (not nested in any ComboBox).
    combo_edits: set[int] = set()
    for combo in native_dlg.descendants(control_type="ComboBox"):
        try:
            for e in combo.descendants(control_type="Edit"):
                combo_edits.add(e.handle or id(e))
        except Exception:
            continue
    standalone: list[UIAWrapper] = []
    for e in native_dlg.descendants(control_type="Edit"):
        try:
            key = e.handle or id(e)
            if key not in combo_edits:
                standalone.append(e)
        except Exception:
            continue
    if len(standalone) == 1:
        return standalone[0]
    if len(standalone) > 1:
        # Qt usually has exactly one fileNameEdit, but dialogs with extra
        # search/filter inputs could surface multiples. Prefer the one whose
        # auto_id contains "fileName" (Qt's auto-id for QFileDialog's
        # filename input), else fall through to diagnostic dump.
        for e in standalone:
            try:
                if "filename" in (e.element_info.automation_id or "").lower():
                    return e
            except Exception:
                continue

    # Diagnostic dump — neither shape matched. Dump every Edit and every
    # ComboBox so we know what tree we're dealing with.
    dump: list[str] = []
    try:
        for c in native_dlg.descendants(control_type="ComboBox"):
            try:
                aid = c.element_info.automation_id or ""
                t = (c.window_text() or "").strip()
                dump.append(f"ComboBox aid={aid!r} text={t!r}")
            except Exception:
                continue
        for e in native_dlg.descendants(control_type="Edit"):
            try:
                aid = e.element_info.automation_id or ""
                t = (e.window_text() or "").strip()
                dump.append(f"Edit aid={aid!r} text={t!r}")
            except Exception:
                continue
    except Exception:
        pass
    raise RuntimeError(
        "filename Edit not found in dialog (tried native ComboBox>Edit and "
        f"Qt standalone-Edit patterns); tree: {dump!r}"
    )


def save_manifest_via_native_dialog(
    pid: int, target_path: str, dialog_timeout: float = 10
) -> None:
    """Drive the QFileDialog opened by File > Save Manifest Decisions….

    1. Locate the filename Edit (native: ComboBox > Edit; Qt non-native:
       standalone QLineEdit). Both shapes handled by ``_find_filename_edit``.
    2. Set its value via UIA's ValuePattern.SetValue — bypasses keyboard
       (so IMEs like bopomofo can't intercept) and bypasses the
       locale-specific ComboBox label name.
    3. Click the action button (Save/OK), located by structure (native:
       2nd-from-rightmost in bottom row; Qt: topmost in QDialogButtonBox).
       Locale-independent for both.
    4. Poll for the artifact appearing on disk (success), a "Save
       Manifest Error" dialog (raise), or timeout (raise diagnostic).

    CI dialog-driving (#129): hosted Windows runners cannot drive the
    native IFileSaveDialog (COM modal loop doesn't pump WM_*; no real
    synthesized input). The ``qa-batch`` workflow sets
    ``PHOTO_MANAGER_QT_FILE_DIALOG=1`` so ``main.py`` opts the entire
    process into Qt's non-native widget dialog, which responds to UIA
    normally. Locally, env var unset → native dialog as users see it.
    Same scenario, both platforms.
    """
    save_hwnd = wait_for_dialog(pid, "Save Manifest Decisions", timeout=dialog_timeout)
    save_dlg = connect_by_handle(save_hwnd)
    _focus(save_dlg)
    time.sleep(0.5)

    filename_edit = _find_filename_edit(save_dlg)
    # Set value via UIA's ValuePattern — bypasses keyboard, focus, and IME.
    # Avoids both IME interception (bopomofo, etc.) and the locale-specific
    # name of the filename ComboBox label.
    filename_edit.iface_value.SetValue(str(target_path))
    # 0.8s gives the native Save dialog time to validate the filename
    # and enable its action button. On a desktop session 0.2s was
    # enough; CI runners are slower and at 0.2s the Save click landed
    # before validation completed.
    time.sleep(0.8)
    # Click the action button by structure (locale-independent — works
    # whether the button reads "Save" or "存檔"). Click rather than
    # send_keys("{ENTER}") because send_keys delivers globally to
    # whatever Windows says is foreground; on a real desktop the dialog
    # IS foreground but on the GitHub-hosted windows-latest runner that
    # invariant doesn't hold and the Enter misses. See #129 for the
    # full diagnostic of why the native-dialog path fails on hosted
    # runners and why the ``PHOTO_MANAGER_QT_FILE_DIALOG`` env var is
    # the canonical fix for CI.
    save_btn = _find_native_dialog_action_button(save_dlg)
    _focus(save_dlg)
    save_btn.click_input()

    # Poll until one of three end states. The success signal is the
    # ARTIFACT existing on disk (not just the dialog closing) — Qt's
    # save handler writes the file after the dialog returns from
    # getSaveFileName, so a dialog-close-only check can return before
    # the write completes and leave callers seeing a missing file.
    #
    #   1. The target file appears on disk → Save fully completed.
    #   2. "Save Manifest Error" appears → raise with the body text.
    #   3. Neither, within the grace window → diagnose: if the Save
    #      dialog is still open, Enter was lost; otherwise the save
    #      attempt was accepted but produced no file (shouldn't happen).
    target_p = Path(target_path)
    grace = min(5.0, dialog_timeout)
    deadline = time.time() + grace
    error_hwnd = None
    while time.time() < deadline:
        if target_p.exists():
            return  # save fully completed; file is on disk
        for hwnd, _cls, t in list_process_windows(pid):
            if t == "Save Manifest Error":
                error_hwnd = hwnd
                break
        if error_hwnd:
            break
        time.sleep(0.2)
    if error_hwnd is None:
        titles_now = [t for _, _, t in list_process_windows(pid)]
        if "Save Manifest Decisions" in titles_now:
            raise RuntimeError(
                f"Save Manifest dialog still open after {grace}s — Enter "
                "likely missed the dialog (focus drift) or the OK button "
                "never enabled (filename validation timing)"
            )
        raise RuntimeError(
            f"Save Manifest dialog closed but {target_p} did not appear "
            f"within {grace}s after Enter — save attempt was accepted "
            "but produced no file"
        )

    error_dlg = connect_by_handle(error_hwnd)
    for label in error_dlg.descendants(control_type="Text"):
        try:
            txt = (label.window_text() or "").strip()
            if txt:
                print(f"  error_text: {txt}", flush=True)
        except Exception:
            continue
    _focus(error_dlg)
    time.sleep(0.2)
    send_keys("{ENTER}")
    time.sleep(0.3)
    raise RuntimeError("Save dialog reported an error — see error_text above")


def open_manifest_via_native_dialog(
    pid: int, target_path: str, dialog_timeout: float = 10
) -> str:
    """Drive the native QFileDialog opened by File > Open Manifest….

    Mirrors save_manifest_via_native_dialog: drive filename via UIA's
    ValuePattern (bypasses IME and locale label drift), press Enter to
    accept. The manifest load is async (ManifestLoadWorker); this helper
    polls actively for the result so the caller doesn't have to race the
    3000ms default status-bar timeout.

    Returns the status bar text observed at success ("Opened manifest: …")
    so the caller can assert on it without re-polling. Raises RuntimeError
    if the load failed (Open Manifest Error dialog appeared) or if neither
    a success-status nor an error-dialog appeared within the grace window.
    """
    from pywinauto.keyboard import send_keys

    open_hwnd = wait_for_dialog(pid, "Open Manifest", timeout=dialog_timeout)
    open_dlg = connect_by_handle(open_hwnd)
    _focus(open_dlg)
    time.sleep(0.5)

    filename_edit = _find_filename_edit(open_dlg)
    filename_edit.iface_value.SetValue(str(target_path))
    time.sleep(0.2)
    send_keys("{ENTER}")

    # Poll for either a success status-bar transition or an error dialog.
    # Active polling avoids the race where the worker's 3000ms-timeout
    # status message expires before the caller gets a chance to read it.
    grace = min(10.0, dialog_timeout)
    deadline = time.time() + grace
    last_status = ""
    while time.time() < deadline:
        # Error window?
        for hwnd, _cls, t in list_process_windows(pid):
            if t == "Open Manifest Error":
                error_dlg = connect_by_handle(hwnd)
                for label in error_dlg.descendants(control_type="Text"):
                    try:
                        txt = (label.window_text() or "").strip()
                        if txt:
                            print(f"  error_text: {txt}", flush=True)
                    except Exception:
                        continue
                _focus(error_dlg)
                time.sleep(0.2)
                send_keys("{ENTER}")
                time.sleep(0.3)
                raise RuntimeError(
                    "Open Manifest dialog reported an error — see error_text above"
                )
        # Status bar settled to success?
        try:
            main_app = Application(backend="uia").connect(
                title_re=WINDOW_TITLE_RE, timeout=0.5
            )
            main_win = main_app.top_window()
            last_status = read_status_bar_text(main_win)
            if "Opened manifest:" in last_status:
                return last_status
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(
        f"open manifest did not complete within {grace}s; "
        f"last_status={last_status!r}"
    )


def open_execute_action_dialog(win: UIAWrapper) -> tuple[UIAWrapper, int]:
    """Open Action > Execute Action… and return (dialog_wrapper, dialog_hwnd)."""
    pid = win.process_id()
    menu_path(win, MENU_ACTION, ACTION_EXECUTE)
    hwnd = wait_for_dialog(pid, EXECUTE_DIALOG_TITLE, timeout=5)
    return connect_by_handle(hwnd), hwnd


def _find_descendant_by_aid_suffix(
    parent: UIAWrapper, control_type: str, suffix: str
) -> UIAWrapper | None:
    """Return the first descendant of ``parent`` whose automation_id ends
    with ``suffix`` and matches ``control_type``, or ``None`` if absent.

    pywinauto exposes Qt's QObject objectName as the trailing path
    segment of the auto_id (e.g. ``QApplication.ActionDialog.QSplitter
    .QWidget.regexLineEdit``). Suffix-matching keeps lookups stable
    when wrapper widgets get inserted around the named control.
    """
    for d in parent.descendants(control_type=control_type):
        try:
            aid = d.element_info.automation_id or ""
        except Exception:
            aid = ""
        if aid.endswith(suffix):
            return d
    return None


def drive_lock_confirm(
    pid: int, verdict: str, timeout: float = 5.0
) -> bool:
    """Wait for the LockedRowsConfirmDialog (#182) and click ``verdict``.

    ``verdict`` is a button label — pass one of
    ``LOCK_CONFIRM_APPLY_ALL_UNLOCKED`` / ``LOCK_CONFIRM_APPLY_UNLOCKED_ONLY``
    / ``LOCK_CONFIRM_CANCEL`` (aliases for the button text constants).

    Returns True if the dialog was found and dismissed, False if no
    dialog appeared within ``timeout``. Use this AFTER an Apply (regex
    flow) or Execute (Execute Action dialog) when locked rows are
    expected to be in the affected set.
    """
    try:
        hwnd = wait_for_dialog(pid, LOCK_CONFIRM_TITLE, timeout=timeout)
    except TimeoutError:
        return False
    dlg = connect_by_handle(hwnd)
    btn = _find_dialog_button(dlg, verdict)
    btn.click_input()
    time.sleep(0.3)
    return True


def drive_delete_regex_confirm(
    pid: int, confirm: bool = True, timeout: float = 1.5
) -> bool:
    """Wait for the DeleteRegexConfirmDialog (D3 from #350, Wave 10) and
    click Confirm (default) or Cancel.

    Returns True if the dialog was found and dismissed, False if no
    dialog appeared within ``timeout`` — short timeout (1.5s) because
    most callers call this unconditionally after Apply and the dialog
    only appears on the delete action path. A missing dialog is the
    common case (non-delete action), not an error.

    The confirm button label is "Mark {matched} files for deletion"
    with a variable count, so we walk dialog descendants and match on
    the "Mark " prefix rather than a literal title. Cancel button uses
    the exact label.
    """
    try:
        hwnd = wait_for_dialog(pid, DELETE_CONFIRM_TITLE, timeout=timeout)
    except TimeoutError:
        return False
    dlg = connect_by_handle(hwnd)
    if confirm:
        # Find the "Delete N files" button by walking descendants and
        # matching the prefix — variable count rules out literal label.
        target = None
        for btn in dlg.descendants(control_type="Button"):
            text = (btn.window_text() or "").strip()
            if text.startswith(_DELETE_CONFIRM_BTN_CONFIRM_PREFIX):
                target = btn
                break
        if target is None:
            raise RuntimeError(
                f"DeleteRegexConfirmDialog: confirm button (prefix "
                f"{_DELETE_CONFIRM_BTN_CONFIRM_PREFIX!r}) not found"
            )
        target.click_input()
    else:
        btn = _find_dialog_button(dlg, DELETE_CONFIRM_BTN_CANCEL)
        btn.click_input()
    time.sleep(0.3)
    return True


def _drive_action_dialog_form(
    action_dlg: UIAWrapper,
    field: str,
    regex: str,
    action_label: str,
    expect_lock_confirm: str | None = None,
) -> str | None:
    """Fill the Set Action by Field dialog and submit.

    Shared by both entry points (menu-bar standalone and Execute-dialog
    inner). Caller must have already focused `action_dlg`.

    Steps: switch dialog to Regex mode if the Phase B toggle exists →
    select Field combo → SetValue regex → wait for live-preview
    debounce → select Action combo → Apply → capture match-counter
    text → Close. Regex uses UIA ValuePattern to bypass IME interception
    of Latin keystrokes under bopomofo input.

    Returns the live-preview match-counter text (e.g. "3 of 5 match")
    captured AFTER the 150 ms debounce window — or ``None`` if the
    dialog has no preview pane (legacy callers that opened ActionDialog
    without ``match_fn``). Scenarios can assert on this to verify the
    preview is reachable and reflects the typed pattern.
    """
    # Phase B introduced a Simple / Regex mode toggle that defaults
    # to Simple (originally named "Beginner"; renamed in Phase C). This
    # helper drives the regex line edit / Set Action combo / Apply
    # button, all of which only make sense in Regex mode (Simple hides
    # the regex line entirely). Click the Regex radio
    # if it exists; without the toggle (no match_fn → flat layout) the
    # dialog is permanently Regex-only and we skip cleanly.
    for radio in action_dlg.descendants(control_type="RadioButton"):
        try:
            aid = radio.element_info.automation_id or ""
        except Exception:
            aid = ""
        # Phase C renamed the Simple radio's objectName from
        # ``regexModeBeginner`` to ``regexModeSimple``; the Regex radio
        # name is stable. We only key off the Regex side here.
        if aid.endswith(".regexModeRegex"):
            try:
                already = False
                try:
                    already = radio.is_selected()
                except Exception:
                    already = False
                if not already:
                    radio.click_input()
                    time.sleep(0.2)
            except Exception:
                pass
            break

    # Find the named widgets by auto_id suffix (Phase A pinned
    # objectName values for exactly this purpose). Falling back to
    # geometry would now give the wrong combo because Simple mode
    # also has its own op combo.
    field_combo = _find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexFieldCombo"
    )
    if field_combo is None:
        raise RuntimeError("action dialog: regexFieldCombo not found")
    field_combo.select(field)
    time.sleep(0.1)

    regex_edit = _find_descendant_by_aid_suffix(
        action_dlg, "Edit", ".regexLineEdit"
    )
    if regex_edit is None:
        raise RuntimeError("action dialog: regexLineEdit not found")
    regex_edit.iface_value.SetValue(regex)

    # Wait past the 150 ms live-preview debounce so the counter has
    # populated before we read it (post-Apply, below).
    time.sleep(0.3)

    action_combo = _find_descendant_by_aid_suffix(
        action_dlg, "ComboBox", ".regexActionCombo"
    )
    if action_combo is None:
        raise RuntimeError("action dialog: regexActionCombo not found")
    # combo.select(text) works reliably locally but flaked on the
    # hosted CI runner specifically for non-default items: s29
    # (action='remove from list', third item) failed twice in a row
    # while s14/s30 (action='delete', first item) always passed in
    # the same runs. Mechanism unclear (possibly a focus or dropdown-
    # visibility race that's invisible under local Windows), so we
    # defend by retrying with a focus refresh between attempts and
    # verifying the combo's displayed text actually changed.
    for attempt in range(3):
        try:
            action_combo.set_focus()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            action_combo.select(action_label)
        except Exception:
            pass
        time.sleep(0.4)
        try:
            current = (action_combo.window_text() or "").strip()
        except Exception:
            current = ""
        if current == action_label:
            break
    else:
        # Final fallback: ValuePattern.SetValue. This sets the visible
        # text but does NOT fire QComboBox::currentIndexChanged on Qt's
        # side — kept as a last-ditch so the helper at least surfaces
        # a clearer failure if the click+verify path can't agree.
        try:
            action_combo.iface_value.SetValue(action_label)
        except Exception:
            pass
        time.sleep(0.2)

    # Use _find_dialog_button (bottom-most match) — on en-US Windows the
    # title-bar Close button shares the form Close's accessible name and a
    # plain child_window lookup goes ambiguous.
    apply_btn = _find_dialog_button(action_dlg, ACTION_DIALOG_BTN_APPLY)
    apply_btn.click_input()
    time.sleep(0.3)

    # Delete-confirm interstitial — D3 from #350 (Wave 10). The Apply
    # handler opens DeleteRegexConfirmDialog modally when the chosen
    # action is "delete" and a live-preview count is available; auto-
    # dismiss with Confirm so existing scenarios that exercise the
    # regex+delete flow stay green. The short timeout (1.5s) means
    # this is effectively a no-op for non-delete actions where the
    # modal never appears. Scenarios that want to test the cancel path
    # explicitly call drive_delete_regex_confirm(pid, confirm=False)
    # BEFORE this helper runs (rare — that case is fully covered by
    # tests/test_select_dialog.py::TestD3DeleteConfirm).
    drive_delete_regex_confirm(action_dlg.process_id(), confirm=True)

    # Lock-confirm interstitial — photo-manager#182. The Apply handler
    # opens LockedRowsConfirmDialog modally when any matched row is
    # locked; we drive it here so the rest of the helper (counter read
    # + Close) sees the action dialog in its post-apply state.
    if expect_lock_confirm is not None:
        drive_lock_confirm(action_dlg.process_id(), expect_lock_confirm)

    # Counter readback happens AFTER Apply — Apply doesn't dismiss the
    # dialog, so the live-preview pane is still on screen with the same
    # numbers. Reading it before Apply meant the descendants() walk
    # interleaved with combo selection, which raced against the
    # dropdown-commit timing on hosted CI runners and caused intermittent
    # action_label='remove from list' applies to silently miss.
    # pywinauto's auto_id is the full QObject hierarchy path ending in
    # objectName, so we suffix-match rather than exact-match — keeps the
    # lookup stable if a wrapper widget gets inserted later.
    counter_text: str | None = None
    try:
        counter = _find_descendant_by_aid_suffix(
            action_dlg, "Text", ".regexMatchCounter"
        )
        if counter is not None:
            counter_text = counter.window_text() or None
    except Exception:
        counter_text = None

    close_action_dialog(action_dlg)

    return counter_text


def _click_btn_and_wait_for_dialog(
    btn: UIAWrapper,
    parent_dlg: UIAWrapper,
    pid: int,
    dialog_title: str,
    attempts: int = 3,
    per_attempt_timeout: float = 4.0,
) -> int:
    """Click a button and wait for a window titled ``dialog_title`` in pid.

    Retries the click if the dialog does not appear within
    ``per_attempt_timeout`` seconds. Used by helpers whose flake mode is
    "click was swallowed and no dialog appeared" — the same family as
    ``open_menu``'s popup-didn't-appear race, but for buttons that spawn
    a dialog rather than a menu popup.

    Re-clicking when the dialog has actually opened is a no-op (the
    button is behind a modal child dialog), so the retry is safe.
    """
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            _focus(parent_dlg)
            btn.click_input()
        except Exception as exc:
            last_err = exc
            time.sleep(0.3)
            continue
        try:
            return wait_for_dialog(pid, dialog_title, timeout=per_attempt_timeout)
        except TimeoutError as exc:
            last_err = exc
            time.sleep(0.3)
    raise TimeoutError(
        f"dialog {dialog_title!r} did not appear after {attempts} click "
        f"attempts (last error: {last_err!r})"
    )


def mark_all_via_regex(
    execute_dlg: UIAWrapper,
    field: str,
    regex: str,
    action_label: str,
    dialog_timeout: float = 5,
    expect_lock_confirm: str | None = None,
) -> str | None:
    """Open the inner Set Action by Field dialog from inside the
    Execute Action dialog, set field+regex+action, click Apply, then Close.

    `field` is the visible text in the Field combo (e.g. "File Name").
    `regex` is set via UIA's ValuePattern to bypass IME (see save-manifest
    helper for the same rationale).
    `action_label` is the visible label in the Set Action combo
    (e.g. "delete" — see SETTABLE_DECISIONS in app/views/constants.py).

    ``expect_lock_confirm`` (#182): see :func:`mark_all_via_regex_standalone`.

    Returns the live-preview match-counter text or ``None`` (see
    ``_drive_action_dialog_form``).
    """
    pid = execute_dlg.process_id()
    select_btn = execute_dlg.child_window(
        title=EXECUTE_BTN_SELECT_BY_REGEX, control_type="Button"
    )
    action_hwnd = _click_btn_and_wait_for_dialog(
        select_btn, execute_dlg, pid, ACTION_DIALOG_TITLE,
        per_attempt_timeout=dialog_timeout,
    )
    action_dlg = connect_by_handle(action_hwnd)
    _focus(action_dlg)
    time.sleep(0.3)

    return _drive_action_dialog_form(
        action_dlg, field, regex, action_label,
        expect_lock_confirm=expect_lock_confirm,
    )


def mark_all_via_regex_standalone(
    main_win: UIAWrapper,
    field: str,
    regex: str,
    action_label: str,
    dialog_timeout: float = 5,
    expect_lock_confirm: str | None = None,
) -> str | None:
    """Drive the standalone Set Action by Field flow from the menu bar.

    Distinct from `mark_all_via_regex` — this opens the dialog via
    Action menu → "Set Action by Field…" (no Execute Action dialog
    in the picture). After Close, focus returns to the main window
    rather than the Execute dialog.

    Use for s14 (standalone Set Action) and any future scenario that
    exercises bulk-decision assignment without entering Execute review.

    ``expect_lock_confirm`` (#182): pass one of the
    ``LOCK_CONFIRM_*`` verdict identifiers when the regex is expected
    to match at least one locked row. The helper drives the modal
    LockedRowsConfirmDialog between Apply and Close. Default ``None``
    keeps the legacy contract (no locked rows in the affected set).

    Returns the live-preview match-counter text or ``None`` (see
    ``_drive_action_dialog_form``).
    """
    pid = main_win.process_id()
    menu_path(main_win, MENU_ACTION, ACTION_BY_REGEX)

    action_hwnd = wait_for_dialog(pid, ACTION_DIALOG_TITLE, timeout=dialog_timeout)
    action_dlg = connect_by_handle(action_hwnd)
    _focus(action_dlg)
    time.sleep(0.3)

    return _drive_action_dialog_form(
        action_dlg, field, regex, action_label,
        expect_lock_confirm=expect_lock_confirm,
    )


def execute_and_confirm(
    execute_dlg: UIAWrapper,
    dialog_timeout: float = 10,
    on_confirm_open=None,
    expect_lock_confirm: str | None = None,
) -> None:
    """Click Execute on the Execute Action dialog, then Yes on the
    'All Files Will Be Deleted' confirmation QMessageBox.

    *on_confirm_open*, if provided, is called with the open confirmation
    dialog wrapper before Yes is clicked. Used by the destructive-confirm
    invariant probe to inspect the dialog's shape (Yes/No buttons, body).

    *expect_lock_confirm* (#182): pass one of the ``LOCK_CONFIRM_*``
    verdict identifiers when locked rows have ``decision='delete'`` at
    Execute time. Under the new lock semantic the pre-execute scan
    surfaces the unified LockedRowsConfirmDialog BEFORE the
    all-delete confirm — drive it here so the remainder of the
    flow (all-delete confirm + dialog close) matches the existing
    happy-path shape. Default ``None`` preserves the pre-#182
    contract (no locked rows in the delete set).

    Returns when the Execute Action dialog has accepted (closed) — that's
    the signal that send2trash + mark_executed have completed.
    """
    pid = execute_dlg.process_id()
    execute_btn = execute_dlg.child_window(title=EXECUTE_BTN, control_type="Button")
    if expect_lock_confirm is not None:
        # Lock-confirm path: click Execute manually, drive the
        # interstitial dialog, then look for the all-delete confirm.
        execute_btn.click_input()
        time.sleep(0.3)
        if not drive_lock_confirm(pid, expect_lock_confirm, timeout=dialog_timeout):
            raise TimeoutError(
                "expect_lock_confirm was set but the LockedRowsConfirmDialog "
                "did not appear after clicking Execute"
            )
        confirm_hwnd = wait_for_dialog(
            pid, EXECUTE_CONFIRM_TITLE, timeout=dialog_timeout
        )
    else:
        confirm_hwnd = _click_btn_and_wait_for_dialog(
            execute_btn, execute_dlg, pid, EXECUTE_CONFIRM_TITLE,
            per_attempt_timeout=dialog_timeout,
        )
    confirm_dlg = connect_by_handle(confirm_hwnd)
    if on_confirm_open is not None:
        try:
            on_confirm_open(confirm_dlg)
        except Exception as exc:
            print(f"  on_confirm_open raised: {exc!r}")
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


def close_scan_dialog_via_close_button(dlg: UIAWrapper) -> None:
    """Click the regular ``Close`` button (the one wired to ``self.reject``).

    Distinct from ``close_and_load_manifest`` which clicks ``Close & Load``
    on the post-success path. Use this on the empty-scan or failed-scan
    paths where no manifest was produced — that's the canonical user exit
    after #86 wired focus to this button.
    """
    # _find_dialog_button picks the bottom-most "Close" — disambiguates
    # against the en-US title-bar Close button whose accessible name is
    # also "Close" (zh-TW renders it as "關閉" so locally there's only
    # one match either way).
    btn = _find_dialog_button(dlg, "Close")
    _focus(dlg)
    btn.invoke()
    time.sleep(0.5)


def focused_button_name(dlg: UIAWrapper) -> str:
    """Return the title of whatever Button currently has UIA focus in dlg.

    Returns ``""`` if no Button is focused. Used by s02 to assert that the
    Close button is the focus target after an empty-scan completion (#86).
    """
    for btn in dlg.descendants(control_type="Button"):
        try:
            if btn.has_keyboard_focus():
                return (btn.window_text() or "").strip()
        except Exception:
            continue
    return ""


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


# ---------------------------------------------------------------------------
# Context-menu helpers (s15) — right-click on result-tree rows, navigate
# cascading popup menus.
# ---------------------------------------------------------------------------

# Context-menu labels — set by ContextMenuHandler from
# app/views/constants.SETTABLE_DECISIONS. English-only; no Qt translations.
CTX_SET_ACTION = "Set Action"
CTX_DELETE = "delete"
CTX_KEEP = "keep"
CTX_OPEN_FOLDER = "Open Folder"
# Lock / Unlock items sit at the top level of the file-row context
# menu (not under the Set Action submenu) — see
# ContextMenuHandler._create_single_selection_menu and the
# multi-select variant. Translation keys: context_menu.lock /
# context_menu.unlock in translations/en.yml.
CTX_LOCK = "Lock"
CTX_UNLOCK = "Unlock"

_VK_CONTROL = 0x11
_VK_DOWN = 0x28
_VK_UP = 0x26
_KEYEVENTF_KEYUP = 0x0002


def _key_down(vk: int) -> None:
    _user32.keybd_event(vk, 0, 0, 0)


def _key_up(vk: int) -> None:
    _user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


def _list_popup_hwnds(pid: int) -> list[int]:
    """Return all popup-class top-level windows owned by pid."""
    return [hwnd for hwnd, cls, _ in list_process_windows(pid) if "Popup" in cls]


def _result_tree(win: UIAWrapper) -> UIAWrapper:
    """Return the main result QTreeView (the largest visible Tree control).

    The main window has one TreeView showing scan results. Other Tree
    controls only exist inside dialogs (e.g. ScanDialog's filesystem tree)
    which should be closed by the time callers reach for a row anchor.
    Picking the largest-area visible Tree is robust to that — even if a
    transient dialog is open, the result tree still dominates.
    """
    candidates: list[tuple[int, UIAWrapper]] = []
    for t in win.descendants(control_type="Tree"):
        try:
            if not t.is_visible():
                continue
            r = t.rectangle()
            area = max(0, (r.right - r.left)) * max(0, (r.bottom - r.top))
            candidates.append((area, t))
        except Exception:
            continue
    if not candidates:
        raise RuntimeError("no visible Tree control found in main window")
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


def _row_anchor(win: UIAWrapper, basename: str) -> tuple[int, int]:
    """Return screen (cx, cy) for the file row whose cell text equals `basename`.

    Scopes the search to the result tree's own descendants. Robust to
    layout shifts (Ref-tier rows moving to top of group post-#78, header
    height changes, DPI scaling) — no hardcoded screen-Y threshold.
    """
    tree = _result_tree(win)
    items = tree.descendants(control_type="TreeItem")
    for it in items:
        try:
            txt = (it.window_text() or "").strip()
            if txt == basename:
                r = it.rectangle()
                cx = r.left + max(20, (r.right - r.left) // 2)
                cy = r.top + (r.bottom - r.top) // 2
                return cx, cy
        except Exception:
            continue
    raise RuntimeError(
        f"row with basename {basename!r} not found in result tree "
        f"(scanned {len(items)} TreeItem(s))"
    )


def left_click_tree_row(win: UIAWrapper, basename: str) -> None:
    """Left-click the file row whose File Name cell equals `basename`.

    Used to seed selection before right-click — QAbstractItemView's default
    selectionCommand returns NoUpdate for right-click, so without a prior
    left-click `customContextMenuRequested` fires with no selection and the
    handler bails out (see context_menu._on_context_menu).
    """
    import pywinauto.mouse

    cx, cy = _row_anchor(win, basename)
    _focus(win)
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    time.sleep(0.2)


def double_click_tree_row(win: UIAWrapper, basename: str) -> None:
    """Double-click the result-tree row whose cell text equals ``basename``.

    Used by s40 (#143) to drive the doubleClicked dispatcher. ``basename``
    matches either a file name (file row) or a group label like "Group 1"
    (group header row).

    Sequence: focus → single left-click to seed Qt's input-tracking
    state on the target row → settle → Win32 ``PostMessage`` with
    ``WM_LBUTTONDBLCLK`` + ``WM_LBUTTONUP`` to the tree's HWND with
    client-relative coords. Bypasses ``pywinauto.mouse.double_click``
    which sends ``SendInput`` DOWN/UP/DOWN/UP — Qt's QAbstractItemView
    does not reliably collapse those into a ``doubleClicked`` signal
    when injected by a non-foreground process (verified empirically in
    s40 bring-up; the synthetic events arrive but Qt processes them as
    two singles).
    """
    import pywinauto.mouse

    cx, cy = _row_anchor(win, basename)
    _focus(win)

    # Seed click — registers row selection / focus the same way a real
    # user's first click would. The doubleClicked signal handler reads
    # the cell's index from the current view state, so this also
    # ensures Qt's mouseTracking state is current on the second press.
    pywinauto.mouse.click(button="left", coords=(cx, cy))
    time.sleep(0.15)

    # PostMessage path. Tree's viewport HWND is what receives
    # WM_LBUTTONDBLCLK — find it via the highest-level main window
    # (locating the QTreeView's own native HWND is brittle when Qt
    # widgets don't always have one) and convert screen coords to
    # client coords via ScreenToClient on the receiver.
    hwnd = win.handle
    pt = ctypes.wintypes.POINT(cx, cy)
    _user32.ScreenToClient(hwnd, ctypes.byref(pt))
    lparam = (pt.y & 0xFFFF) << 16 | (pt.x & 0xFFFF)
    WM_LBUTTONDBLCLK = 0x0203
    WM_LBUTTONUP = 0x0202
    MK_LBUTTON = 0x0001
    _user32.PostMessageW(hwnd, WM_LBUTTONDBLCLK, MK_LBUTTON, lparam)
    _user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    time.sleep(0.3)


def find_tree_item(win: UIAWrapper, text: str) -> UIAWrapper:
    """Return the TreeItem in the result tree whose text equals ``text``.

    Used by s40 to read ``is_expanded()`` on group header rows. Raises
    if the item isn't found — caller is expected to assert presence
    after a model load.
    """
    tree = _result_tree(win)
    for it in tree.descendants(control_type="TreeItem"):
        try:
            if (it.window_text() or "").strip() == text:
                return it
        except Exception:
            continue
    raise RuntimeError(f"TreeItem with text {text!r} not found in result tree")


def ctrl_click_tree_row(win: UIAWrapper, basename: str) -> None:
    """Ctrl+click the file row to extend selection (ExtendedSelection mode).

    Uses Win32 keybd_event for the modifier so it bypasses any IME
    interception on Latin keystrokes (per the bopomofo rule in CLAUDE.md;
    modifier keys aren't intercepted but we use the same primitive
    everywhere for consistency).
    """
    import pywinauto.mouse

    cx, cy = _row_anchor(win, basename)
    _focus(win)
    _key_down(_VK_CONTROL)
    try:
        pywinauto.mouse.click(button="left", coords=(cx, cy))
    finally:
        _key_up(_VK_CONTROL)
    time.sleep(0.2)


def right_click_tree_row(win: UIAWrapper, basename: str) -> None:
    """Right-click the file row whose File Name cell equals `basename`.

    Caller is responsible for any prior selection setup (left-click or
    ctrl-click). After this call, the QMenu popup is open and ready for
    `select_popup_menu_path`.

    Retries up to 3 times if the popup doesn't appear — same flake mode
    as ``open_menu`` (foreground/timing race swallowing the click). Sends
    Esc between attempts in case a stuck menu-bar-active state from a
    swallowed click is blocking the next one.
    """
    import pywinauto.mouse

    cx, cy = _row_anchor(win, basename)
    pid = win.process_id()
    for attempt in range(3):
        _focus(win)
        pywinauto.mouse.right_click(coords=(cx, cy))
        time.sleep(0.4)
        if _list_popup_hwnds(pid):
            return
        # No popup — reset any stuck state before retrying.
        _user32.keybd_event(0x1B, 0, 0, 0)
        _user32.keybd_event(0x1B, 0, 2, 0)
        time.sleep(0.3)


def select_popup_menu_path(
    pid: int, labels: list[str], timeout: float = 5
) -> None:
    """Navigate a chain of popup menus by accessible-name labels.

    Expects a Qt popup to already be open (e.g. after `right_click_tree_row`).
    Each label is clicked in succession; between non-leaf clicks, waits for
    a NEW popup window (different hwnd than any previously seen) to appear,
    then descends into it. Submenus are top-level Win32 popup windows in
    Qt, not nested QWidgets, so we navigate by hwnd.
    """
    if not labels:
        raise ValueError("labels must be non-empty")

    seen: set[int] = set()
    deadline = time.time() + timeout

    cur_hwnd: int | None = None
    while time.time() < deadline:
        popups = _list_popup_hwnds(pid)
        if popups:
            cur_hwnd = popups[0]
            break
        time.sleep(0.1)
    if cur_hwnd is None:
        raise TimeoutError("no popup window appeared")
    seen.add(cur_hwnd)

    for i, label in enumerate(labels):
        popup = connect_by_handle(cur_hwnd)
        popup.child_window(title=label, control_type="MenuItem").click_input()
        time.sleep(0.3)
        if i == len(labels) - 1:
            return  # leaf clicked; menu auto-dismisses

        # Wait for the submenu (a fresh popup hwnd not in `seen`).
        sub_hwnd: int | None = None
        sub_deadline = time.time() + 3
        while time.time() < sub_deadline:
            for hwnd in _list_popup_hwnds(pid):
                if hwnd not in seen:
                    sub_hwnd = hwnd
                    break
            if sub_hwnd is not None:
                break
            time.sleep(0.1)
        if sub_hwnd is None:
            raise TimeoutError(
                f"submenu for {label!r} did not appear within 3s"
            )
        seen.add(sub_hwnd)
        cur_hwnd = sub_hwnd
