from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from app.views.constants import (
    COL_FOLDER,
    COL_GROUP_COUNT,
    COL_NAME,
    COL_SEL,
    COL_SIZE_BYTES,
    HEADERS,
    PATH_ROLE,
    SORT_ROLE,
)


def build_model(
    groups: Iterable[object],
) -> tuple[QStandardItemModel, QSortFilterProxyModel | None]:
    """Builds the tree model and a proxy for sorting with roles.

    Returns (model, proxy). Proxy can be None on failure.
    """
    model = QStandardItemModel()
    model.setHorizontalHeaderLabels(HEADERS)

    for g in groups:
        group_number = int(getattr(g, "group_number", 0) or 0)
        group_item = QStandardItem(f"Group {group_number}")
        group_item.setEditable(False)
        try:
            group_item.setData(group_number, SORT_ROLE)
        except Exception:
            pass

        group_count_val = len(getattr(g, "items", []) or [])
        group_row = [
            group_item,
            QStandardItem(""),
            QStandardItem(""),
            QStandardItem(""),
            QStandardItem(""),
            QStandardItem(str(group_count_val)),
        ]
        try:
            group_row[COL_GROUP_COUNT].setData(int(group_count_val), SORT_ROLE)
        except Exception:
            pass
        for it in group_row:
            it.setEditable(False)
        model.appendRow(group_row)

        for p in getattr(g, "items", []) or []:
            name = Path(getattr(p, "file_path", "")).name
            folder = getattr(p, "folder_path", "")
            size_num = int(getattr(p, "file_size_bytes", 0) or 0)
            check = QStandardItem("")
            check.setCheckable(True)
            check.setEditable(False)
            # Initialize checkbox from model's is_mark
            try:
                is_marked = bool(getattr(p, "is_mark", False))
                check.setCheckState(Qt.Checked if is_marked else Qt.Unchecked)
            except Exception:
                pass
            child_row = [
                QStandardItem(""),
                check,
                QStandardItem(name),
                QStandardItem(folder),
                QStandardItem(str(size_num)),
                QStandardItem(""),
            ]
            try:
                child_row[COL_SEL].setData(0, SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_NAME].setData(str(name).lower(), SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_FOLDER].setData(str(folder).lower(), SORT_ROLE)
            except Exception:
                pass
            try:
                child_row[COL_SIZE_BYTES].setData(int(size_num), SORT_ROLE)
            except Exception:
                pass
            for it in child_row:
                it.setEditable(False)
            try:
                child_row[COL_NAME].setData(getattr(p, "file_path", ""), PATH_ROLE)
            except Exception:
                pass
            group_item.appendRow(child_row)

    # Install proxy for numeric/text sort with roles
    try:
        proxy = QSortFilterProxyModel()
        proxy.setSortRole(SORT_ROLE)
        proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
        proxy.setSourceModel(model)
    except Exception:
        proxy = None

    return model, proxy
