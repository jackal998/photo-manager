"""
UI/view constants centralized for reuse across view modules.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from infrastructure.i18n import t

# Column indices stay as integer constants — they're not user-facing.
# COL_LOCK was added between COL_ACTION and COL_NAME in #182 so the
# lock state is its own sortable / searchable column instead of a 🔒
# prefix glyph on the Action column. Indices below 2 are unchanged;
# indices above 2 shifted by +1.
COL_GROUP: int = 0
COL_ACTION: int = 1
COL_LOCK: int = 2
COL_NAME: int = 3
COL_FOLDER: int = 4
COL_SIZE_BYTES: int = 5
COL_GROUP_COUNT: int = 6
COL_CREATION_DATE: int = 7
COL_SHOT_DATE: int = 8
COL_RESOLUTION: int = 9
# COL_SCORE was appended at index 10 for #187 (keep-worthiness scoring).
# All prior column indices are unchanged so existing sort-state config
# in settings.json and any user muscle memory keep working.
COL_SCORE: int = 10
NUM_COLUMNS: int = 11


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
        t("column.lock"),
        t("column.file_name"),
        t("column.folder"),
        t("column.size_bytes"),
        t("column.group_count"),
        t("column.creation_date"),
        t("column.shot_date"),
        t("column.resolution"),
        t("column.score"),
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

# Sentinels for lock / unlock dispatched through the same regex / multi-
# select code path as decisions. They DO NOT update ``user_decision`` —
# they flip the orthogonal ``is_locked`` flag. Callers in
# ``file_operations`` recognise these sentinels and route to
# ``set_locked_state`` instead of ``set_decision``. Bulk regex applies
# them to all matched rows (idempotent — no skip-locked pre-filter on
# the lock/unlock route itself). See photo-manager#164.
LOCK_SENTINEL: str = "__lock__"
UNLOCK_SENTINEL: str = "__unlock__"


def settable_decisions(
    include_remove: bool = False,
    include_lock: bool = False,
) -> list[tuple[str, str]]:
    """User-settable decision options for context menus and ActionDialog.

    Each tuple is ``(display_label, stored_value)``. The stored value is
    internal (``"delete"`` or empty string for "keep — remove action");
    only the label is translated.

    When ``include_remove`` is True, appends an entry whose stored value
    is :data:`REMOVE_FROM_LIST_SENTINEL`. When ``include_lock`` is True,
    appends two more entries with the lock/unlock sentinels. Callers
    that recognise the sentinels route to the appropriate backend
    instead of the decision-update path. Defaults are both False so the
    main-window right-click submenu (which has separate top-level Lock /
    Unlock items) doesn't gain duplicate entries.
    """
    decisions: list[tuple[str, str]] = [
        (t("decision.delete"), "delete"),
        (t("decision.keep"), ""),
    ]
    if include_remove:
        decisions.append((t("decision.remove_from_list"), REMOVE_FROM_LIST_SENTINEL))
    if include_lock:
        decisions.append((t("decision.lock"), LOCK_SENTINEL))
        decisions.append((t("decision.unlock"), UNLOCK_SENTINEL))
    return decisions
