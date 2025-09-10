from __future__ import annotations

from typing import Any
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel

from .constants import COL_GROUP, COL_SEL, COL_NAME, COL_FOLDER, COL_SIZE_BYTES


def apply_select_regex(model: QStandardItemModel, field: str, pattern: str, make_checked: bool) -> None:
    """Apply selection/unselection based on regex match.

    - `field` is one of the UI headers (Group, File Name, Folder, Size (Bytes)).
    - Applies only to file rows; when `field == Group`, applies to all children
      of matching groups.
    - `make_checked=True` selects, `False` unselects.
    """
    rx = re.compile(pattern)

    root_count = model.rowCount()
    for r in range(root_count):
        parent_item = model.item(r, COL_GROUP)
        if parent_item is None:
            continue
        if field == "Group":
            group_text = parent_item.text() or ""
            if rx.search(group_text or ""):
                for cr in range(parent_item.rowCount()):
                    check_item = parent_item.child(cr, COL_SEL)
                    if check_item is not None and check_item.isCheckable():
                        check_item.setCheckState(Qt.Checked if make_checked else Qt.Unchecked)
            continue

        # Else match per child
        for cr in range(parent_item.rowCount()):
            if field == "File Name":
                item = parent_item.child(cr, COL_NAME)
            elif field == "Folder":
                item = parent_item.child(cr, COL_FOLDER)
            elif field == "Size (Bytes)":
                item = parent_item.child(cr, COL_SIZE_BYTES)
            else:
                item = None
            target_text = item.text() if item else ""
            if target_text and rx.search(target_text):
                check_item = parent_item.child(cr, COL_SEL)
                if check_item is not None and check_item.isCheckable():
                    check_item.setCheckState(Qt.Checked if make_checked else Qt.Unchecked)


