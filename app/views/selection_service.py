"""UI adapter to apply regex-based selection to a Qt `QStandardItemModel`."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel

from app.views.constants import (
    COL_ACTION,
    COL_CREATION_DATE,
    COL_FOLDER,
    COL_GROUP,
    COL_GROUP_COUNT,
    COL_NAME,
    COL_SEL,
    COL_SHOT_DATE,
    COL_SIZE_BYTES,
)
from core.services.selection_service import RegexSelectionService

# Fields that live on the group row; a regex match selects every child in the group.
_GROUP_LEVEL_FIELDS: frozenset[str] = frozenset({"Match", "Group Count"})

# Mapping from display field name to column index for file-level fields.
_FILE_COL: dict[str, int] = {
    "Action": COL_ACTION,
    "File Name": COL_NAME,
    "Folder": COL_FOLDER,
    "Size (Bytes)": COL_SIZE_BYTES,
    "Creation Date": COL_CREATION_DATE,
    "Shot Date": COL_SHOT_DATE,
}


def apply_select_regex(
    model: QStandardItemModel, field: str, pattern: str, make_checked: bool
) -> None:
    """Apply selection/unselection to `model` where `field` matches `pattern`.

    Args:
        model: Qt standard item model containing group/child rows.
        field: One of the field names listed in dialog_handler.FIELDS.
        pattern: Regular expression to match against the target field.
        make_checked: If True, set checked; otherwise uncheck.
    """

    class _QtModelAccessor:
        def __init__(self, m: QStandardItemModel) -> None:
            self._m = m

        def iter_groups(self) -> list[Any]:
            return [
                self._m.item(r, COL_GROUP)
                for r in range(self._m.rowCount())
                if self._m.item(r, COL_GROUP) is not None
            ]

        def iter_children(self, group: Any) -> list[int]:
            return list(range(group.rowCount()))

        def get_field_text(self, group: Any, child: Any, field_name: str) -> str | None:
            """Return text to match against.

            Returns None for file-level fields when child is None (signals to
            the service that this field should be handled at child level).
            """
            if child is None:
                if field_name not in _GROUP_LEVEL_FIELDS:
                    return None  # file-level field — service will iterate children
                if field_name == "Match":
                    return group.text() or ""
                if field_name == "Group Count":
                    row = group.index().row()
                    item = self._m.item(row, COL_GROUP_COUNT)
                    return item.text() if item is not None else ""
                return None

            col = _FILE_COL.get(field_name)
            if col is None:
                return ""
            item = group.child(int(child), col)
            return item.text() if item is not None else ""

        def set_checked(self, group: Any, child: Any, checked: bool) -> None:
            check_item = group.child(int(child), COL_SEL)
            if check_item is not None and check_item.isCheckable():
                check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    service = RegexSelectionService(_QtModelAccessor(model))
    service.apply(field, pattern, make_checked)
