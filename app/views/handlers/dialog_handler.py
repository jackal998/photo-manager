"""DialogHandler: Coordinates dialog operations and user interactions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from app.views.constants import (
    COL_ACTION,
    COL_CREATION_DATE,
    COL_FOLDER,
    COL_GROUP,
    COL_GROUP_COUNT,
    COL_NAME,
    COL_SHOT_DATE,
    COL_SIZE_BYTES,
)

# Maps tree column index → dialog field name.
_COL_TO_FIELD: dict[int, str] = {
    COL_GROUP:         "Similarity",
    COL_ACTION:        "Action",
    COL_NAME:          "File Name",
    COL_FOLDER:        "Folder",
    COL_SIZE_BYTES:    "Size (Bytes)",
    COL_GROUP_COUNT:   "Group Count",
    COL_CREATION_DATE: "Creation Date",
    COL_SHOT_DATE:     "Shot Date",
}


class TreeDataProvider(Protocol):
    """Protocol for tree data provider."""

    def get_selection_model(self):
        """Get tree selection model."""
        ...

    def get_view_model(self):
        """Get tree view model."""
        ...

    def get_source_model(self):
        """Get source model."""
        ...

    def get_proxy_model(self):
        """Get proxy model."""
        ...


class DialogHandler:
    """Coordinates dialog operations and user interactions."""

    def __init__(
        self,
        parent_widget: QObject,
        tree_data_provider: TreeDataProvider,
        action_handler: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.parent = parent_widget
        self.tree_provider = tree_data_provider
        self.action_handler = action_handler

    def show_action_dialog(self, clicked_col: int | None = None) -> None:
        """Show the Set Action by Field/Regex dialog."""
        try:
            from app.views.dialogs.select_dialog import ActionDialog
        except Exception:
            QMessageBox.critical(self.parent, "Set Action — Internal Error", "Action dialog not available.")
            return

        fields = [
            "Similarity",
            "Action",
            "File Name",
            "Folder",
            "Size (Bytes)",
            "Group Count",
            "Creation Date",
            "Shot Date",
        ]

        initial_field = _COL_TO_FIELD.get(clicked_col) if clicked_col is not None else None
        row_values = self._get_highlighted_row_values()
        dlg = ActionDialog(
            fields=fields, parent=self.parent,
            row_values=row_values, initial_field=initial_field,
        )

        if self.action_handler is not None:
            dlg.setActionRequested.connect(self.action_handler)

        dlg.exec()

    # Backward-compatibility alias
    def show_select_dialog(self, clicked_col: int | None = None) -> None:
        self.show_action_dialog(clicked_col=clicked_col)

    def _get_highlighted_row_values(self) -> dict[str, str]:
        """Get values from the currently highlighted row for dialog pre-population."""
        values: dict[str, str] = {}
        try:
            sel = self.tree_provider.get_selection_model()
            if not sel:
                return values

            rows = sel.selectedRows()
            if not rows:
                return values

            idx = rows[0]
            view_model = self.tree_provider.get_view_model()
            src_model = self.tree_provider.get_source_model()
            proxy = self.tree_provider.get_proxy_model()

            if proxy is not None and hasattr(proxy, "mapToSource"):
                idx = proxy.mapToSource(idx)
                model = src_model
            else:
                model = view_model

            if idx.parent().isValid():
                parent_idx = idx.parent()

                def _gcol(col: int) -> str:
                    return model.data(model.index(idx.row(), col, parent_idx)) or ""

                def _group_col(col: int) -> str:
                    return model.data(model.index(parent_idx.row(), col, parent_idx.parent())) or ""

                values["Similarity"] = _group_col(COL_GROUP)
                values["Group Count"] = _group_col(COL_GROUP_COUNT)
                values["Action"] = _gcol(COL_ACTION)
                values["File Name"] = _gcol(COL_NAME)
                values["Folder"] = _gcol(COL_FOLDER)
                values["Size (Bytes)"] = _gcol(COL_SIZE_BYTES)
                values["Creation Date"] = _gcol(COL_CREATION_DATE)
                values["Shot Date"] = _gcol(COL_SHOT_DATE)
            else:
                def _top_col(col: int) -> str:
                    return model.data(model.index(idx.row(), col)) or ""

                values["Similarity"] = _top_col(COL_GROUP)
                values["Group Count"] = _top_col(COL_GROUP_COUNT)
        except Exception:
            pass

        return values
