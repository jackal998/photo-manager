from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel

from .constants import COL_GROUP, COL_SEL, COL_NAME, COL_FOLDER, COL_SIZE_BYTES
from core.services.selection_service import RegexSelectionService


def apply_select_regex(model: QStandardItemModel, field: str, pattern: str, make_checked: bool) -> None:
    """Apply selection/unselection based on regex match via Core service.

    Behavior is identical to the previous inline implementation.
    """

    class _QtModelAccessor:
        def __init__(self, m: QStandardItemModel) -> None:
            self._m = m

        def iter_groups(self) -> list[Any]:
            return [self._m.item(r, COL_GROUP) for r in range(self._m.rowCount()) if self._m.item(r, COL_GROUP) is not None]

        def iter_children(self, group: Any) -> list[int]:
            return list(range(group.rowCount()))

        def get_field_text(self, group: Any, child: Any, field_name: str) -> str:
            if child is None:
                return group.text() or ""
            if field_name == "File Name":
                item = group.child(int(child), COL_NAME)
            elif field_name == "Folder":
                item = group.child(int(child), COL_FOLDER)
            elif field_name == "Size (Bytes)":
                item = group.child(int(child), COL_SIZE_BYTES)
            elif field_name == "Group":
                return group.text() or ""
            else:
                item = None
            return item.text() if item is not None else ""

        def set_checked(self, group: Any, child: Any, checked: bool) -> None:
            check_item = group.child(int(child), COL_SEL)
            if check_item is not None and check_item.isCheckable():
                check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    service = RegexSelectionService(_QtModelAccessor(model))
    service.apply(field, pattern, make_checked)


