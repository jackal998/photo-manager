"""data-testid constants for the photo-manager web UI.

Playwright locators use ``page.get_by_test_id(CONSTANT)`` so that
renaming a label in the HTML only requires updating this file —
no grep-and-replace across driver scripts.

Naming convention
-----------------
- ``MAIN_*``  — elements on the main result-list page
- ``SCAN_*``  — elements in the scan dialog / scan progress panel
- ``EXEC_*``  — elements in the execute-action dialog
- ``DLGE_*``  — elements in other dialogs (settings, confirm, etc.)
- ``ROW_*``   — helpers for per-row locators in the result tree

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
# Execute-action dialog
# ---------------------------------------------------------------------------

EXEC_DIALOG = "exec-dialog"
"""The execute-action dialog wrapper."""

EXEC_ACTION_SELECT = "exec-action-select"
"""<select> element for choosing the action (delete / move / …)."""

EXEC_REGEX_INPUT = "exec-regex-input"
"""Regex filter input inside the execute dialog."""

EXEC_PREVIEW_TABLE = "exec-preview-table"
"""Table showing which files will be affected by the current action."""

EXEC_CONFIRM_BUTTON = "exec-confirm-button"
"""'Apply' / 'Execute' confirm button inside the execute dialog."""

EXEC_CANCEL_BUTTON = "exec-cancel-button"
"""'Cancel' button inside the execute dialog."""

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
# Confirmation dialogs
# ---------------------------------------------------------------------------

DLGE_CONFIRM_OK = "confirm-ok-button"
"""Primary confirm button used in generic confirm dialogs."""

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
