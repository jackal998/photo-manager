"""data-testid constants for the photo-manager web UI.

Playwright locators use ``page.get_by_test_id(CONSTANT)`` so that
renaming a label in the HTML only requires updating this file —
no grep-and-replace across driver scripts.

Naming convention
-----------------
- ``MAIN_*``      — elements on the main result-list page
- ``SCAN_*``      — elements in the scan dialog / scan progress panel
- ``EXECUTE_*``   — elements in the execute-action dialog (§5 canonical)
- ``ACTION_*``    — elements in the action dialog (Set Action by Field, §5.4 contract)
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

MAIN_STATUS_ERROR = "main-status-error"
"""Additive error line in the footer, below MAIN_STATUS_BAR.  Renders the
store's ``manifest.error`` (a failed manifest load / decision / lock / prune /
save) as a ``role="alert"`` line so the failure is visible to the user instead
of being written to a field nothing renders (#712).  Conditional — absent when
``manifest.error`` is null."""

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
"""Toolbar button that loads the manifest path typed in the adjacent input.
The filesystem *browser* is a separate affordance (File → Open Manifest and
the empty-state 'Open Manifest…' button → FsBrowser file mode)."""

MAIN_EMPTY_STATE = "main-empty-state"
"""Placeholder shown in the main content area when no manifest is loaded."""

MAIN_EMPTY_SCAN = "main-empty-scan"
"""'Scan…' action button inside the empty-state placeholder."""

MAIN_EMPTY_OPEN = "main-empty-open"
"""'Open Manifest…' action button inside the empty-state placeholder."""

# ---------------------------------------------------------------------------
# Menu bar (Qt MenuController parity — File / Action / View)
# ---------------------------------------------------------------------------

MAIN_MENU_BAR = "main-menu-bar"
"""The top menu bar container (mirrors Qt's QMenuBar)."""

MENU_FILE = "menu-file"
"""'File' top-level menu trigger."""

MENU_FILE_SCAN = "menu-file-scan"
"""File → Scan Sources… menu item (opens the scan dialog)."""

MENU_FILE_OPEN = "menu-file-open"
"""File → Open Manifest… menu item (opens the filesystem file picker)."""

MENU_ACTION = "menu-action"
"""'Action' top-level menu trigger."""

MENU_ACTION_SET = "menu-action-set"
"""Action → Set Action by Regex menu item (second ActionDialog entry point)."""

MENU_ACTION_EXECUTE = "menu-action-execute"
"""Action → Execute… menu item (opens the execute-action dialog)."""

MENU_ACTION_EXECUTE_SELECTED = "menu-action-execute-selected"
"""Action → Execute (only selected)… menu item.

Gated on a non-empty main-tree multi-selection. Opens the execute-action
dialog scoped to the selected rows' groups (#430 group-pull: selecting any
row pulls its whole group into the dialog)."""

MENU_VIEW = "menu-view"
"""'View' top-level menu trigger."""

MENU_VIEW_LANG_EN = "menu-view-lang-en"
"""View → Language → English menu item."""

MENU_VIEW_LANG_ZH = "menu-view-lang-zh"
"""View → Language → 中文 (zh_TW) menu item."""

# ---------------------------------------------------------------------------
# Filesystem picker (FsBrowser — directory / file / save modes)
# ---------------------------------------------------------------------------

FS_BROWSER = "fs-browser"
"""The filesystem picker dialog wrapper."""

FS_BROWSER_UP = "fs-browser-up"
"""'Up' button — navigates to the parent directory (or roots)."""

FS_BROWSER_PATH = "fs-browser-path"
"""Current-directory display in the picker header."""

FS_BROWSER_ENTRY = "fs-browser-entry"
"""A directory or file row in the picker list (shared testid; filter by text).
``data-kind`` is ``"dir"`` or ``"file"``."""

FS_BROWSER_FILENAME = "fs-browser-filename"
"""Filename input shown in save mode (the output .db name)."""

FS_BROWSER_CONFIRM = "fs-browser-confirm"
"""Confirm button (Select folder / Open / Save depending on mode)."""

FS_BROWSER_CANCEL = "fs-browser-cancel"
"""Cancel button in the filesystem picker."""

FS_BROWSER_ERROR = "fs-browser-error"
"""Error text shown when a directory cannot be listed."""

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

SCAN_OUTPUT_BROWSE = "scan-output-browse"
"""'Browse…' button next to the output-path field (opens FsBrowser save mode)."""

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

SCAN_ADVANCED = "scan-advanced"
"""Collapsible 'Advanced settings' section summary/toggle in the scan dialog."""

SCAN_AUTO_SELECT = "scan-auto-select"
"""'Auto select after scan' checkbox (Advanced settings) — promotes the
top-scored row per group to action=KEEP + is_locked before the manifest write."""

SCAN_AGGRESSIVE_DELETE = "scan-aggressive-delete"
"""'Also mark all other files for delete' sub-checkbox — enabled only when
auto-select is on; tags every non-keeper in a scored group user_decision=delete."""

SCAN_AUTOTUNE = "scan-autotune"
"""'Auto-tune reader concurrency' checkbox (Advanced settings) — default ON
(opt-out, #551 Phase 4); never changes grouping."""

SCAN_PHASH_THRESHOLD = "scan-phash-threshold"
"""pHash Similarity Threshold number input (Advanced settings, #736) —
Hamming-distance ceiling (default 10, range 1-20) between two 64-bit pHashes
before two images are grouped as near-duplicates. Sent per-scan as
``WebScanRequest.threshold``; never persisted (mirrors Qt's slider+spinbox,
read at scan-start only)."""

SCAN_DHASH_THRESHOLD = "scan-dhash-threshold"
"""dHash Confidence Threshold number input (Advanced settings, #736) —
Hamming-distance ceiling (default 10, range 1-20) for the second,
independent perceptual hash that confirms a pHash near-duplicate as
high-confidence. Sent per-scan as ``WebScanRequest.dhash_threshold``; never
persisted."""

SCAN_COLOR_THRESHOLD = "scan-color-threshold"
"""Mean Color Gate number input (Advanced settings, #736) — L2 distance
ceiling (default 30, range 0-100) between two images' average RGB colors;
above it a pHash match is excluded from grouping (0 = disabled). Sent
per-scan as ``WebScanRequest.mean_color_threshold``; never persisted."""

SCAN_RESCAN_CONFIRM_DIALOG = "scan-rescan-confirm-dialog"
"""'Discard pending decisions?' confirmation shown when Start Scan is clicked
while the loaded manifest has staged (un-executed) decisions a re-scan would
discard.  Nested modal over the scan dialog.  Mirrors Qt's
MainWindow._confirm_no_pending_decisions QMessageBox — s27 (#142)."""

SCAN_RESCAN_CONFIRM_CANCEL = "scan-rescan-confirm-cancel"
"""'Cancel' button in the rescan-confirm dialog (the safe default — keeps the
pending decisions and leaves the scan dialog open, no scan)."""

SCAN_RESCAN_CONFIRM_DISCARD = "scan-rescan-confirm-discard"
"""'Discard & Rescan' button — destructive: proceeds with the scan, which
rebuilds the manifest and clears the pending decisions."""

# ---------------------------------------------------------------------------
# Execute-action dialog (§5 canonical names — Phase 2C2)
# ---------------------------------------------------------------------------

EXECUTE_DIALOG = "execute-dialog"
"""The execute-action dialog wrapper."""

EXECUTE_ALL_DELETE_BANNER = "execute-all-delete-banner"
"""Warning banner shown when every scoped row is a delete."""

EXECUTE_HIDDEN_DESTRUCTIVE_BANNER = "execute-hidden-destructive-banner"
"""Warning banner shown when the active type filter HIDES pending delete
rows (filter='ignore').  Mirrors Qt's #502 hidden-destructive line so the
user is told staged deletes exist but aren't visible/committed under the
current filter.  See #676."""

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

EXECUTE_REMOVE_CONFIRM = "execute-remove-confirm"
"""Confirmation sheet shown before the execute-dialog row menu's 'Remove from
list' finalizes a row (outcome='ignored', no disk delete).  Mirrors Qt's
'Remove from List' QMessageBox (default No) in execute_action_dialog.py."""

EXECUTE_REMOVE_CONFIRM_YES = "execute-remove-confirm-yes"
"""'Yes, remove' button inside the remove-from-list confirmation sheet."""

EXECUTE_REMOVE_CONFIRM_NO = "execute-remove-confirm-no"
"""'Cancel' / 'No' button inside the remove-from-list confirmation sheet
(the default — destructive-ish action requires an explicit Yes)."""

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

PRUNE_BTN_REMOVE = "prune-btn-remove"
"""'Remove {N}' — confirm the prune (#686). Label count is dynamic."""

PRUNE_BTN_KEEP = "prune-btn-keep"
"""'Keep all' — dismiss the prune dialog without pruning the unlocked buckets."""

PRUNE_INCLUDE_ACTIONED = "prune-include-actioned"
"""Actioned-bucket opt-in checkbox — only rendered in the MIXED layout
(both plain and actioned singletons present), default unchecked (#686)."""

PRUNE_REMEMBER = "prune-remember"
"""'Don't ask again' checkbox — Remove+remember flips the pref to 'always',
Keep+remember flips it to 'never' (#686)."""

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

CTX_SET_ACTION_BY_FIELD = "ctx-set-action-by-field"
"""Context-menu item: open the Set Action by Field (ActionDialog) bulk-decide
flow.  Present on the execute-dialog row menu AND the result-tree file-row and
group-row menus (#735); maps to ``store.openActionDialog(initialField?)`` —
the same dialog the toolbar 'Set Action' button opens, optionally pre-filled
from the right-clicked column.  Mirrors Qt's 'Set Action by Field…' item
(execute_action_dialog.py:810, context_menu.py)."""

CTX_EXECUTE_SELECTED = "ctx-execute-selected"
"""Context-menu item (file-row variant only, #735): 'Execute Action (only
selected)…' — invokes the same execute-selected-only flow as the menu-bar
Action entry (re-reads the live selection). Mirrors Qt's
context_menu.py file-row menu / action_handlers.py:85-99. Absent from the
group-row reduced menu."""

# ---------------------------------------------------------------------------
# Action dialog (Set Action by Field — §5.4 contract)
# ---------------------------------------------------------------------------

ACTION_MAIN_BUTTON = "main-action-button"
"""Toolbar entry point that opens the action dialog."""

ACTION_DIALOG = "action-dialog"
"""The action dialog wrapper (Set Action by Field)."""

ACTION_FIELD_COMBO = "action-field-combo"
"""Combo box for selecting the field to match against (File Name, Score, …)."""

ACTION_SIMPLE_ROW = "action-simple-row"
"""Container row for the Simple mode inputs (prefix label + op combo + text input)."""

ACTION_SIMPLE_OP = "action-simple-op"
"""Simple mode operator combo (contains / starts with / ends with / exactly matches)."""

ACTION_SIMPLE_TEXT = "action-simple-text"
"""Simple mode text input (the literal text to match)."""

ACTION_SIMPLE_DISABLED_NOTE = "action-simple-disabled-note"
"""Note shown above Simple inputs when write-through preview is unavailable."""

ACTION_REGEX_ROW = "action-regex-row"
"""Container row for the Regex mode input."""

ACTION_REGEX_INPUT = "action-regex-input"
"""Regex input field."""

ACTION_VALIDATION_ICON = "action-validation-icon"
"""Icon (tick / cross) indicating whether the current regex is valid."""

ACTION_VALIDATION_ERROR = "action-validation-error"
"""Error text shown when the regex or threshold input is invalid."""

ACTION_NUMERIC_ROW = "action-numeric-row"
"""Container row for the numeric condition panel (shown for numeric fields)."""

ACTION_NUMERIC_MODE_THRESHOLD = "action-numeric-mode-threshold"
"""Radio / toggle selecting Threshold mode in the numeric panel."""

ACTION_NUMERIC_MODE_TOPN = "action-numeric-mode-topn"
"""Radio / toggle selecting Top-N per group mode in the numeric panel."""

ACTION_NUMERIC_CMP = "action-numeric-cmp"
"""Threshold comparison operator combo (> / >= / < / <= / == / !=)."""

ACTION_NUMERIC_VALUE = "action-numeric-value"
"""Threshold value input (number or YYYY-MM-DD for date fields)."""

ACTION_NUMERIC_ERROR = "action-numeric-error"
"""Error text shown when the threshold value cannot be parsed."""

ACTION_NUMERIC_ORDER = "action-numeric-order"
"""Top-N order combo (Top / Bottom)."""

ACTION_NUMERIC_N = "action-numeric-n"
"""Top-N count input (the N in "Top N per group")."""

ACTION_MATCH_COUNTER = "action-match-counter"
"""Live match counter showing "{matched} of {total} match" (or Top-N variant)."""

ACTION_ACTION_COMBO = "action-action-combo"
"""Combo box for selecting the action to apply to each matched row (delete / keep / …)."""

ACTION_BTN_APPLY = "action-btn-apply"
"""Primary 'Apply' button that fires POST /api/action/bulk-decide."""

ACTION_PREVIEW_LIST = "action-preview-list"
"""Scrollable list of sample affected filenames / paths from the last preview."""

ACTION_PREVIEW_TRUNCATED = "action-preview-truncated"
"""Note shown below the preview list when the server truncated the sample."""

# --- ActionDialog inline lock-conflict dialog (#674) -----------------------
# Distinct from the execute-flow ``LOCK_CONFIRM_*`` family below: this is the
# ActionDialog (Set Action by Field) bulk-decide flow's own three-verdict
# lock dialog (Unlock & Apply All / Apply to Unlocked Only / Cancel). The two
# dialogs are never open at once; the ``action-`` prefix keeps the wire values
# unambiguous.
ACTION_LOCK_CONFIRM_DIALOG = "action-lock-confirm-dialog"
"""Wrapper for the ActionDialog inline lock-conflict dialog (bulk-decide 409)."""

ACTION_LOCK_CONFIRM_BTN_UNLOCK_APPLY = "action-lock-confirm-btn-unlock-apply"
"""'Unlock & Apply' — re-POST bulk-decide with force_locked=true."""

ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY = "action-lock-confirm-btn-unlocked-only"
"""'Apply to Unlocked Only' — re-POST bulk-decide with skip_locked=true.
Disabled when every matched row is locked (no unlocked subset)."""

ACTION_LOCK_CONFIRM_BTN_CANCEL = "action-lock-confirm-btn-cancel"
"""'Cancel' — dismiss the inline lock dialog, write nothing."""

# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

DLGE_SETTINGS_DIALOG = "settings-dialog"
"""Settings dialog wrapper."""

DLGE_SETTINGS_SAVE = "settings-save-button"
"""'Save' button inside the settings dialog."""

DLGE_SETTINGS_CANCEL = "settings-cancel-button"
"""'Cancel' button inside the settings dialog."""

DLGE_SETTINGS_PRUNE_SELECT = "settings-prune-select"
"""3-value ui.prune_singletons control (ask / always / never) (#686)."""

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


def scan_source_browse_testid(idx: int) -> str:
    """Return the ``data-testid`` for the 'Browse…' button of the i-th source row.

    Opens the FsBrowser in directory mode to pick the source folder.

    Parameters
    ----------
    idx:
        0-based position of the source row in the ScanDialog source list.

    Returns
    -------
    str
        ``"scan-source-{idx}-browse"``
    """
    return f"scan-source-{idx}-browse"


# ---------------------------------------------------------------------------
# Execute file-row helper
# ---------------------------------------------------------------------------


def action_cheatsheet_testid(token: str) -> str:
    """Return the ``data-testid`` for a cheatsheet chip in the action dialog.

    Each chip in the Regex cheatsheet section has a unique testid that
    encodes the token it inserts.  The token value is the raw insertion
    text (e.g. ``".*"``, ``"\\\\d"``, ``"^"``, ``"$"``, ``"\\\\."`` etc.).

    Parameters
    ----------
    token:
        The insertion text for the chip (e.g. ``".*"``, ``"\\\\d"``).

    Returns
    -------
    str
        ``"action-cheatsheet-{token}"``
    """
    return f"action-cheatsheet-{token}"


def col_header_testid(col_id: str) -> str:
    """Return the ``data-testid`` for a result-tree column header cell.

    The sticky column-header row (#685) renders one cell per metadata column
    (``name`` / ``similarity`` / ``action`` / ``score`` / ``dims`` / ``size`` /
    ``date`` — the ids from ``frontend/src/lib/resultColumns.ts``). Sortable
    cells (``name`` / ``size``) toggle the sort when clicked; s45 drives them.

    Parameters
    ----------
    col_id:
        The column id (e.g. ``"name"``, ``"size"``).

    Returns
    -------
    str
        ``"col-header-{col_id}"``
    """
    return f"col-header-{col_id}"


def col_resize_testid(col_id: str) -> str:
    """Return the ``data-testid`` for a column's right-edge resize handle.

    Each header cell exposes a resize grab strip; s47 drags ``name``'s handle
    to a known width and asserts the width restores after a reload.

    Parameters
    ----------
    col_id:
        The column id whose resize handle this is (e.g. ``"name"``).

    Returns
    -------
    str
        ``"col-resize-{col_id}"``
    """
    return f"col-resize-{col_id}"


# ---------------------------------------------------------------------------
# Group grid (multi-tile video feature)
# ---------------------------------------------------------------------------

GRID_CONTAINER = "grid-container"
"""Outer div of the GroupGrid tile grid.  Present when a group row is selected
in the result tree and the preview pane switches to grid mode."""

# ---------------------------------------------------------------------------
# GroupMediaController bar elements (GMC_*)
# ---------------------------------------------------------------------------

GMC_BAR = "gmc-bar"
"""Outer div of the GroupMediaController control bar — the canonical testid for
the controller bar rendered below the tile grid when the group contains at
least one video tile."""

GMC_PLAY_PAUSE = "gmc-play-pause"
"""Play / pause button in the GroupMediaController bar.  Clicking broadcasts
play or pause to all registered video tiles via a majority-vote algorithm."""

GMC_PROGRESS_SLIDER = "gmc-progress-slider"
"""Master progress range input in the GroupMediaController bar.  Dragging and
releasing seeks all registered video tiles to the slider position (in ms)."""

GMC_CURRENT_TIME = "gmc-current-time"
"""Current-time span in the GroupMediaController bar.  Displays the master
playback position as MM:SS (or HH:MM:SS when duration exceeds one hour)."""

GMC_DURATION = "gmc-duration"
"""Total-duration span in the GroupMediaController bar.  Displays the maximum
duration across all registered video tiles as MM:SS / HH:MM:SS."""

GMC_MUTE_BUTTON = "gmc-mute-button"
"""Mute toggle button in the GroupMediaController bar.  Clicking toggles the
shared volume between 0 (muted) and the last non-zero value (default 50/100)."""

GMC_VOLUME_SLIDER = "gmc-volume-slider"
"""Volume range input (0–100) in the GroupMediaController bar.  Changing the
slider broadcasts the new volume (divided by 100) to all registered tiles."""


def grid_video_tile_testid(group_id: str, basename: str) -> str:
    """Return the ``data-testid`` value for a VideoTile inside GroupGrid.

    Parameters
    ----------
    group_id:
        The string form of ``group_number`` (use ``str(group_number)``).
    basename:
        The filename component only — *not* the full path.

    Returns
    -------
    str
        ``"grid-video-tile-{group_id}-{basename}"``
    """
    return f"grid-video-tile-{group_id}-{basename}"


def grid_image_tile_testid(group_id: str, basename: str) -> str:
    """Return the ``data-testid`` value for an ImageTile inside GroupGrid.

    Parameters
    ----------
    group_id:
        The string form of ``group_number`` (use ``str(group_number)``).
    basename:
        The filename component only — *not* the full path.

    Returns
    -------
    str
        ``"grid-image-tile-{group_id}-{basename}"``
    """
    return f"grid-image-tile-{group_id}-{basename}"


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
