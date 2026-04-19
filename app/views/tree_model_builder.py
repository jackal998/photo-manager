from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from app.views.constants import (
    COL_ACTION,
    COL_CREATION_DATE,
    COL_FOLDER,
    COL_GROUP_COUNT,
    COL_NAME,
    COL_SEL,
    COL_SHOT_DATE,
    COL_SIZE_BYTES,
    HEADERS,
    PATH_ROLE,
    SORT_ROLE,
)

_ACTION_TO_MATCH = {
    "EXACT": "exact",
    "REVIEW_DUPLICATE": "similar",
}


def _group_match_label(items: list) -> str:
    """Derive group-level match label from its files."""
    for item in items:
        if getattr(item, "action", "") == "EXACT":
            return "exact"
    for item in items:
        if getattr(item, "action", "") == "REVIEW_DUPLICATE":
            return "similar"
    return ""


def _file_match_label(action: str, group_items: list) -> str:
    """Derive file-level match label; reference files inherit from siblings."""
    label = _ACTION_TO_MATCH.get(action, "")
    if label:
        return label
    if action == "":  # reference role — inherit from sibling candidate
        for sibling in group_items:
            sib = getattr(sibling, "action", "")
            inherited = _ACTION_TO_MATCH.get(sib, "")
            if inherited:
                return inherited
    return ""


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
        items_list = getattr(g, "items", []) or []

        # Col 0 at group row shows the overall match type for the group
        group_match = _group_match_label(items_list)
        group_item = QStandardItem(group_match)
        group_item.setEditable(False)
        try:
            group_item.setData(group_number, SORT_ROLE)
        except Exception:
            pass

        group_count_val = len(items_list)
        group_row = [
            group_item,
            QStandardItem(""),
            QStandardItem(""),
            QStandardItem(""),
            QStandardItem(""),
            QStandardItem(str(group_count_val)),
            QStandardItem(""),
            QStandardItem(""),
            QStandardItem(""),  # COL_ACTION — action is at file level only
        ]
        try:
            group_row[COL_GROUP_COUNT].setData(int(group_count_val), SORT_ROLE)
        except Exception:
            pass
        for it in group_row:
            it.setEditable(False)
        model.appendRow(group_row)

        for p in items_list:
            name = Path(getattr(p, "file_path", "")).name
            folder = getattr(p, "folder_path", "")
            size_num = int(getattr(p, "file_size_bytes", 0) or 0)
            shot_dt = getattr(p, "shot_date", None)
            creation_dt = getattr(p, "creation_date", None)
            shot_txt = shot_dt.strftime("%Y-%m-%d %H:%M:%S") if shot_dt else ""
            creation_txt = creation_dt.strftime("%Y-%m-%d %H:%M:%S") if creation_dt else ""

            # Col 0 at file row: match type, with sibling-inherit for reference files
            file_action = getattr(p, "action", "") or ""
            file_match = _file_match_label(file_action, items_list)

            # Col 8: user's decision (delete / keep / "")
            item_decision = getattr(p, "user_decision", "") or ""

            check = QStandardItem("")
            check.setEditable(False)
            check.setCheckable(True)
            try:
                is_marked = bool(getattr(p, "is_mark", False))
                check.setCheckState(Qt.Checked if is_marked else Qt.Unchecked)
            except Exception:
                pass

            child_row = [
                QStandardItem(file_match),   # COL_GROUP — match type
                check,
                QStandardItem(name),
                QStandardItem(folder),
                QStandardItem(str(size_num)),
                QStandardItem(""),
                QStandardItem(creation_txt),
                QStandardItem(shot_txt),
                QStandardItem(item_decision),  # COL_ACTION — user decision
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
            try:
                child_row[COL_CREATION_DATE].setData(
                    int(creation_dt.timestamp()) if creation_dt else 0, SORT_ROLE
                )
            except Exception:
                pass
            try:
                child_row[COL_SHOT_DATE].setData(
                    int(shot_dt.timestamp()) if shot_dt else 0, SORT_ROLE
                )
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
