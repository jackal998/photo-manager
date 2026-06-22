"""data-testid constants for the photo-manager web UI.

Playwright locators use ``page.get_by_test_id(CONSTANT)`` so that
renaming a label in the HTML only requires updating this file —
no grep-and-replace across driver scripts.

Naming convention
-----------------
- ``MAIN_*``      — elements on the main result-list page
- ``SCAN_*``      — elements in the scan dialog / scan progress panel
- ``EXECUTE_*``   — elements in the execute-action dialog (§5 canonical)
- ``LOCK_*``      — lock-conflict confirmation dialog
- ``DELETE_*``    — destructive-delete confirmation dialog
- ``PRUNE_*``     — prune-singletons confirmation dialog
- ``PREVIEW_*``   — inline preview pane
- ``FULLRES_*``   — full-resolution image dialog
- ``CONTEXT_*``   — right-click context menu
- ``CTX_*``       — individual context-menu items
- ``DLGE_*``      — other dialogs (settings, etc.)
- ``ROW_*``       — helpers for per-row locators in the result tree

All constants are strings.  Keep them in sync with the actual
``data-testid="..."`` attributes set in the frontend templates.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

MAIN_RESULT_TREE = "main-result-tree"
"""The outer container of the grouped-file result tree."""

MAIN_STATUS_BAR = "main-status-bar"
"""Status-bar paragraph shown at the bottom of the main page."""

MAIN_SCAN_BUTTON = "main-scan-button"
"""'Scan' / 'Re-scan' button that opens the scan dialog."""

MAIN_EXECUTE_BUTTON = "main-execute-button"
"""'Execute' button that opens the execute-action dialog."""

MAIN_LANG_TOGGLE = "main-lang-toggle"
"""Language toggle button (EN / ZH)."""

MAIN_SETTINGS_BUTTON = "main-settings-button"
"""Settings / gear button."""

MAIN_MANIFEST_INPUT = "main-manifest-input"
"""Text input for typing or pasting a manifest (.db) file path."""

MAIN_MANIFEST_OPEN = "main-manifest-open-button"
"""Button that opens the filesystem browser to pick a manifest file."""

MAIN_EMPTY_STATE = "main-empty-state"
"""Placeholder shown in the main content area when no manifest is loaded."""

# ---------------------------------------------------------------------------
# Scan dialog / progress panel
# ---------------------------------------------------------------------------

SCAN_ADD_SOURCE = "scan-add-source-button"
"""Button inside the scan dialog that adds a new source path entry."""

SCAN_DIALOG = "scan-dialog"
"""The scan-configuration dialog wrapper."""

SCAN_SOURCE_LIST = "scan-source-list"
"""List of source paths configured for the current scan."""

SCAN_OUTPUT_PATH = "scan-output-path"
"""Output-path input field inside the scan dialog."""

SCAN_START_BUTTON = "scan-start-button"
"""'Start scan' button inside the scan dialog."""

SCAN_CANCEL_BUTTON = "scan-cancel-button"
"""'Cancel' button shown while a scan is running."""

SCAN_PROGRESS_LOG = "scan-progress-log"
"""Scrolling log area that receives SSE ``log`` events."""

SCAN_PROGRESS_BAR = "scan-progress-bar"
"""Progress bar shown during scan (value = completed / total)."""

SCAN_STATUS_TEXT = "scan-status-text"
"""Short status line above the progress bar (stage name, ETA, …)."""

# ---------------------------------------------------------------------------
# Execute-action dialog (§5 canonical names — Phase 2C2)
# ---------------------------------------------------------------------------

EXECUTE_DIALOG = "execute-dialog"
"""The execute-action dialog wrapper."""

EXECUTE_ALL_DELETE_BANNER = "execute-all-delete-banner"
"""Warning banner shown when every scoped row is a delete."""

EXECUTE_TYPE_FILTER = "execute-type-filter"
"""Filter control that restricts the tree to a decision type (all / delete / ignore)."""

EXECUTE_TREE = "execute-tree"
"""Scrollable file-tree inside the execute dialog showing rows to be actioned."""

EXECUTE_BTN_EXECUTE = "execute-btn-execute"
"""Primary 'Execute' button that fires POST /api/execute for all rows."""

EXECUTE_BTN_EXECUTE_SELECTED = "execute-btn-execute-selected"
"""'Execute selected' button that fires POST /api/execute with scope_paths."""

EXECUTE_PREVIEW_PANE = "execute-preview-pane"
"""Thumbnail preview pane on the right side of the execute dialog."""

EXECUTE_PREVIEW_IMAGE = "execute-preview-image"
"""The <img> element inside the execute preview pane."""

EXECUTE_ALL_DELETE_CONFIRM = "execute-all-delete-confirm"
"""'All-delete' safety confirmation sheet (shown when every row is a delete)."""

EXECUTE_ALL_DELETE_CONFIRM_YES = "execute-all-delete-confirm-yes"
"""'Yes, delete all' button inside the all-delete confirmation sheet."""

EXECUTE_ALL_DELETE_CONFIRM_NO = "execute-all-delete-confirm-no"
"""'Cancel' / 'No' button inside the all-delete confirmation sheet."""

# ---------------------------------------------------------------------------
# Lock-conflict confirmation dialog
# ---------------------------------------------------------------------------

LOCK_CONFIRM_DIALOG = "lock-confirm-dialog"
"""Dialog shown when a delete would affect locked rows."""

LOCK_CONFIRM_BTN_UNLOCK_APPLY = "lock-confirm-btn-unlock-apply"
"""'Unlock and apply' button — removes locks then re-runs the operation."""

LOCK_CONFIRM_BTN_UNLOCKED_ONLY = "lock-confirm-btn-unlocked-only"
"""'Apply to unlocked only' button — skips locked rows."""

LOCK_CONFIRM_BTN_CANCEL = "lock-confirm-btn-cancel"
"""'Cancel' button in the lock-conflict dialog."""

# ---------------------------------------------------------------------------
# Destructive-delete confirmation dialog
# ---------------------------------------------------------------------------

DELETE_CONFIRM_DIALOG = "delete-confirm-dialog"
"""Generic 'Are you sure you want to delete?' confirmation dialog."""

DELETE_CONFIRM_BTN_CONFIRM = "delete-confirm-btn-confirm"
"""'Confirm delete' button in the delete confirmation dialog."""

DELETE_CONFIRM_BTN_CANCEL = "delete-confirm-btn-cancel"
"""'Cancel' button in the delete confirmation dialog."""

# ---------------------------------------------------------------------------
# Prune-singletons confirmation dialog
# ---------------------------------------------------------------------------

PRUNE_CONFIRM_DIALOG = "prune-confirm-dialog"
"""Confirmation dialog before pruning singleton groups."""

# ---------------------------------------------------------------------------
# Inline preview pane
# ---------------------------------------------------------------------------

PREVIEW_PANE = "preview-pane"
"""Inline image-preview panel shown to the right of the result tree."""

PREVIEW_SINGLE_IMAGE = "preview-single-image"
"""The <img> element inside the inline preview pane."""

PREVIEW_INFO = "preview-info"
"""Metadata / EXIF info section below the preview image."""

# ---------------------------------------------------------------------------
# Full-resolution image dialog
# ---------------------------------------------------------------------------

FULLRES_DIALOG = "fullres-dialog"
"""Full-resolution image viewer dialog."""

FULLRES_IMAGE = "fullres-image"
"""The <img> element inside the full-res dialog."""

# ---------------------------------------------------------------------------
# Right-click context menu
# ---------------------------------------------------------------------------

CONTEXT_MENU = "context-menu"
"""Context menu shown on right-click over a file row."""

CTX_SET_ACTION_KEEP = "ctx-set-action-keep"
"""Context-menu item: set decision to 'keep' (clear)."""

CTX_SET_ACTION_DELETE = "ctx-set-action-delete"
"""Context-menu item: set decision to 'delete'."""

CTX_SET_ACTION_REMOVE = "ctx-set-action-remove"
"""Context-menu item: set decision to 'remove from list'."""

CTX_OPEN_FOLDER = "ctx-open-folder"
"""Context-menu item: reveal file in Explorer (POST /api/reveal)."""

CTX_LOCK = "ctx-lock"
"""Context-menu item: lock the row."""

CTX_UNLOCK = "ctx-unlock"
"""Context-menu item: unlock the row."""

CTX_APPLY_BEST_COPY = "ctx-apply-best-copy"
"""Context-menu item: apply 'best copy' auto-selection to the group."""

# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

DLGE_SETTINGS_DIALOG = "settings-dialog"
"""Settings dialog wrapper."""

DLGE_SETTINGS_SAVE = "settings-save-button"
"""'Save' button inside the settings dialog."""

DLGE_SETTINGS_CANCEL = "settings-cancel-button"
"""'Cancel' button inside the settings dialog."""

# ---------------------------------------------------------------------------
# Generic confirmation dialogs (kept for settings-level confirmations)
# ---------------------------------------------------------------------------

DLGE_CONFIRM_CANCEL = "confirm-cancel-button"
"""Cancel button in generic confirm dialogs."""

# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def row_file_testid(group_id: str, basename: str) -> str:
    """Return the ``data-testid`` value for a file-row in the result tree.

    Each row in the result tree has a unique testid that encodes both
    the group it belongs to and the file's basename.  Including
    ``group_id`` avoids Playwright strict-mode errors when the same
    filename (e.g. ``shared.jpg``) appears in multiple groups.

    Parameters
    ----------
    group_id:
        The ``group_id`` value from the manifest row (usually a hex
        string or an integer serialised as a string).
    basename:
        The filename component only — *not* the full path.

    Returns
    -------
    str
        ``"row-file-{group_id}-{basename}"``
    """
    return f"row-file-{group_id}-{basename}"


def row_group_testid(group_id: str) -> str:
    """Return the ``data-testid`` value for a group header row.

    Parameters
    ----------
    group_id:
        The ``group_id`` value from the manifest row.

    Returns
    -------
    str
        ``"row-group-{group_id}"``
    """
    return f"row-group-{group_id}"


def row_decision_testid(group_id: str, basename: str) -> str:
    """Return the ``data-testid`` value for a per-row decision control.

    Each file row in the result tree exposes a decision selector (delete /
    ignore / keep) whose testid encodes both the group and the filename so
    Playwright strict-mode never sees duplicate locators.

    Parameters
    ----------
    group_id:
        The string form of ``group_number`` from the manifest Group object
        (use ``str(group_number)``).
    basename:
        The filename component only — *not* the full path.

    Returns
    -------
    str
        ``"row-decision-{group_id}-{basename}"``
    """
    return f"row-decision-{group_id}-{basename}"


def row_lock_testid(group_id: str, basename: str) -> str:
    """Return the ``data-testid`` value for a per-row lock toggle.

    Each file row in the result tree exposes a lock checkbox whose testid
    encodes both the group and the filename so Playwright strict-mode never
    sees duplicate locators.

    Parameters
    ----------
    group_id:
        The string form of ``group_number`` from the manifest Group object
        (use ``str(group_number)``).
    basename:
        The filename component only — *not* the full path.

    Returns
    -------
    str
        ``"row-lock-{group_id}-{basename}"``
    """
    return f"row-lock-{group_id}-{basename}"


def execute_all_delete_jump_testid(group_id: str) -> str:
    """Return the ``data-testid`` value for the jump-to-group link in the
    all-delete warning banner of the execute dialog.

    The banner lists every group that consists entirely of delete rows and
    provides an anchor link so the user can scroll to that group in the
    execute tree.  Each link has a unique testid keyed by ``group_id`` so
    Playwright can target individual groups without strict-mode errors.

    Parameters
    ----------
    group_id:
        The string form of ``group_number`` from the manifest Group object
        (use ``str(group_number)``).

    Returns
    -------
    str
        ``"execute-all-delete-jump-{group_id}"``
    """
    return f"execute-all-delete-jump-{group_id}"


# ---------------------------------------------------------------------------
# Scan source-row helpers (idx = 0-based position in the source list)
# ---------------------------------------------------------------------------


def scan_source_label_testid(idx: int) -> str:
    """Return the ``data-testid`` for the label input of the i-th source row.

    Parameters
    ----------
    idx:
        0-based position of the source row in the ScanDialog source list.

    Returns
    -------
    str
        ``"scan-source-{idx}-label"``
    """
    return f"scan-source-{idx}-label"


def scan_source_path_testid(idx: int) -> str:
    """Return the ``data-testid`` for the path input of the i-th source row.

    Parameters
    ----------
    idx:
        0-based position of the source row in the ScanDialog source list.

    Returns
    -------
    str
        ``"scan-source-{idx}-path"``
    """
    return f"scan-source-{idx}-path"


def scan_source_recursive_testid(idx: int) -> str:
    """Return the ``data-testid`` for the recursive checkbox of the i-th source row.

    Parameters
    ----------
    idx:
        0-based position of the source row in the ScanDialog source list.

    Returns
    -------
    str
        ``"scan-source-{idx}-recursive"``
    """
    return f"scan-source-{idx}-recursive"


# ---------------------------------------------------------------------------
# Execute file-row helper
# ---------------------------------------------------------------------------


def execute_row_testid(group_id: str, basename: str) -> str:
    """Return the ``data-testid`` value for a file row in the execute tree.

    Mirrors the inline template used by ExecuteFileRow in ExecuteTree.tsx
    (``execute-row-{groupId}-{basename}``).  Having a single Python helper
    means scenario drivers never drift from the emitted HTML attribute.

    Parameters
    ----------
    group_id:
        The string form of ``group_number`` from the manifest Group object
        (use ``str(group_number)``).
    basename:
        The filename component only — *not* the full path.

    Returns
    -------
    str
        ``"execute-row-{group_id}-{basename}"``
    """
    return f"execute-row-{group_id}-{basename}"
