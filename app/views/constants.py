"""
UI/view constants centralized for reuse across view modules.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from infrastructure.i18n import t

# Column indices stay as integer constants — they're not user-facing.
COL_GROUP: int = 0
COL_ACTION: int = 1
COL_NAME: int = 2
COL_FOLDER: int = 3
COL_SIZE_BYTES: int = 4
COL_GROUP_COUNT: int = 5
COL_CREATION_DATE: int = 6
COL_SHOT_DATE: int = 7
COL_RESOLUTION: int = 8
NUM_COLUMNS: int = 9


# Data roles
PATH_ROLE: int = Qt.UserRole  # store full path on name item
SORT_ROLE: int = Qt.UserRole + 1  # used by QSortFilterProxyModel


# Preview/grid defaults
DEFAULT_THUMB_SIZE: int = 512  # overridable by settings.json
GRID_MIN_THUMB_PX: int = 200
GRID_SPACING_PX: int = 4
GRID_MARGIN_RATIO: float = 0.05  # left/right and top/bottom


def headers() -> list[str]:
    """Column header labels resolved against the active locale.

    Lazy: each call re-reads the catalog so language changes (after a
    restart) take effect even if this module was imported before
    ``init_translator``.
    """
    return [
        t("column.similarity"),
        t("column.action"),
        t("column.file_name"),
        t("column.folder"),
        t("column.size_bytes"),
        t("column.group_count"),
        t("column.creation_date"),
        t("column.shot_date"),
        t("column.resolution"),
    ]


# Sentinel emitted by the regex / right-click dispatch when the user
# picks "remove from list". The receiving handler routes the sentinel
# differently depending on context:
#   * single-row right-click in the execute dialog → IMMEDIATE
#     (set + execute together, with confirmation), the row vanishes
#     and the manifest is updated.
#   * regex bulk path → DEFERRED, mirroring the delete/keep UX:
#     each matched row's user_decision is set to
#     :data:`REMOVE_FROM_LIST_DECISION` and the user reviews + commits
#     via the execute action dialog.
REMOVE_FROM_LIST_SENTINEL: str = "__remove_from_list__"

# Stored user_decision value for the deferred remove-from-list flow.
# Persisted to SQLite alongside the existing "" / "delete" / "keep"
# values, displayed in the Action column via a localised label, and
# applied at execute time (mark removed in the manifest, drop from vm).
REMOVE_FROM_LIST_DECISION: str = "remove_from_list"


def settable_decisions(include_remove: bool = False) -> list[tuple[str, str]]:
    """User-settable decision options for context menus and ActionDialog.

    Each tuple is ``(display_label, stored_value)``. The stored value is
    internal (``"delete"`` or empty string for "keep — remove action");
    only the label is translated.

    When ``include_remove`` is True, appends a third entry whose stored
    value is :data:`REMOVE_FROM_LIST_SENTINEL`. Callers that recognise
    the sentinel route to the remove-from-list backend instead of the
    decision-update path. Default is False so the main-window
    right-click submenu (which has a separate top-level "Remove from
    List" item) doesn't gain a duplicate entry.
    """
    decisions: list[tuple[str, str]] = [
        (t("decision.delete"), "delete"),
        (t("decision.keep"), ""),
    ]
    if include_remove:
        decisions.append((t("decision.remove_from_list"), REMOVE_FROM_LIST_SENTINEL))
    return decisions
