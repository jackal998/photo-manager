from __future__ import annotations

"""
UI/view constants centralized for reuse across view modules.

This module MUST NOT change any visible text, column order, or sorting
semantics. It only centralizes magic numbers and roles.
"""

from PySide6.QtCore import Qt


# Column headers and indices (order must remain unchanged)
HEADERS: list[str] = [
    "Group",
    "Sel",
    "File Name",
    "Folder",
    "Size (Bytes)",
    "Group Count",
]

COL_GROUP: int = 0
COL_SEL: int = 1
COL_NAME: int = 2
COL_FOLDER: int = 3
COL_SIZE_BYTES: int = 4
COL_GROUP_COUNT: int = 5
NUM_COLUMNS: int = 6


# Data roles
PATH_ROLE: int = Qt.UserRole  # store full path on name item
SORT_ROLE: int = Qt.UserRole + 1  # used by QSortFilterProxyModel


# Preview/grid defaults
DEFAULT_THUMB_SIZE: int = 512  # overridable by settings.json
GRID_MIN_THUMB_PX: int = 200
GRID_SPACING_PX: int = 4
GRID_MARGIN_RATIO: float = 0.05  # left/right and top/bottom


