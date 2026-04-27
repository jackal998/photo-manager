"""
UI/view constants centralized for reuse across view modules.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

# Column headers and indices
HEADERS: list[str] = [
    "Similarity",
    "Sel",
    "Action",
    "File Name",
    "Folder",
    "Size (Bytes)",
    "Group Count",
    "Creation Date",
    "Shot Date",
    "Resolution",
]

COL_GROUP: int = 0
COL_SEL: int = 1
COL_ACTION: int = 2
COL_NAME: int = 3
COL_FOLDER: int = 4
COL_SIZE_BYTES: int = 5
COL_GROUP_COUNT: int = 6
COL_CREATION_DATE: int = 7
COL_SHOT_DATE: int = 8
COL_RESOLUTION: int = 9
NUM_COLUMNS: int = 10


# Data roles
PATH_ROLE: int = Qt.UserRole  # store full path on name item
SORT_ROLE: int = Qt.UserRole + 1  # used by QSortFilterProxyModel


# Preview/grid defaults
DEFAULT_THUMB_SIZE: int = 512  # overridable by settings.json
GRID_MIN_THUMB_PX: int = 200
GRID_SPACING_PX: int = 4
GRID_MARGIN_RATIO: float = 0.05  # left/right and top/bottom


# User-settable decision options used by context menus and SelectDialog.
# Each tuple is (display_label, stored_value).  "keep (remove action)" stores ""
# (empty) — undecided is the natural no-op; the label clarifies intent to the user.
SETTABLE_DECISIONS: list[tuple[str, str]] = [
    ("delete",               "delete"),
    ("keep (remove action)", ""),
]
