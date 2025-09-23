"""
UI/view constants centralized for reuse across view modules.

This module MUST NOT change any visible text, column order, or sorting
semantics. It only centralizes magic numbers and roles.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

# Column headers and indices (base order preserved; new columns appended)
HEADERS: list[str] = [
    "Group",
    "Sel",
    "File Name",
    "Folder",
    "Size (Bytes)",
    "Group Count",
    "Creation Date",
    "Shot Date",
]

COL_GROUP: int = 0
COL_SEL: int = 1
COL_NAME: int = 2
COL_FOLDER: int = 3
COL_SIZE_BYTES: int = 4
COL_GROUP_COUNT: int = 5
COL_CREATION_DATE: int = 6
COL_SHOT_DATE: int = 7
NUM_COLUMNS: int = 8


# Data roles
PATH_ROLE: int = Qt.UserRole  # store full path on name item
SORT_ROLE: int = Qt.UserRole + 1  # used by QSortFilterProxyModel


# Preview/grid defaults
DEFAULT_THUMB_SIZE: int = 512  # overridable by settings.json
GRID_MIN_THUMB_PX: int = 200
GRID_SPACING_PX: int = 4
GRID_MARGIN_RATIO: float = 0.05  # left/right and top/bottom
