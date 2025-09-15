"""DialogHandler: Coordinates dialog operations and user interactions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from app.views.constants import COL_FOLDER, COL_GROUP, COL_NAME, COL_SIZE_BYTES


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
    """Coordinates dialog operations and user interactions.

    This class encapsulates all dialog-related functionality including:
    - Select by field/regex dialog coordination
    - Row value extraction for dialog pre-population
    - Dialog signal connection management
    """

    def __init__(
        self,
        parent_widget: QObject,
        tree_data_provider: TreeDataProvider,
        regex_handler: Callable[[str, str, bool], None],
    ) -> None:
        """Initialize with parent widget and data provider.

        Args:
            parent_widget: Parent widget for dialogs
            tree_data_provider: Provider for tree data access
            regex_handler: Handler for regex selection operations
        """
        self.parent = parent_widget
        self.tree_provider = tree_data_provider
        self.regex_handler = regex_handler

    def show_select_dialog(self) -> None:
        """Show the select by field/regex dialog with current row values."""
        try:
            from app.views.dialogs.select_dialog import SelectDialog
        except Exception:
            QMessageBox.critical(self.parent, "Select", "Select dialog not available.")
            return

        fields = [
            "Group",
            "File Name",
            "Folder",
            "Size (Bytes)",
        ]

        row_values = self._get_highlighted_row_values()
        dlg = SelectDialog(fields=fields, parent=self.parent, row_values=row_values)

        # Connect dialog signals to handlers
        dlg.selectRequested.connect(lambda field, pattern: self.regex_handler(field, pattern, True))
        dlg.unselectRequested.connect(
            lambda field, pattern: self.regex_handler(field, pattern, False)
        )

        dlg.exec()

    def _get_highlighted_row_values(self) -> dict[str, str]:
        """Get values from the currently highlighted row for dialog pre-population.

        Returns:
            Dictionary mapping field names to their values from the selected row
        """
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

            # Handle proxy model mapping
            if proxy is not None and hasattr(proxy, "mapToSource"):
                idx = proxy.mapToSource(idx)
                model = src_model
            else:
                model = view_model

            if idx.parent().isValid():
                # Child row (file) - extract data
                parent_idx = idx.parent()
                group_text = (
                    model.data(model.index(parent_idx.row(), COL_GROUP, parent_idx.parent())) or ""
                )
                name = model.data(model.index(idx.row(), COL_NAME, parent_idx)) or ""
                folder = model.data(model.index(idx.row(), COL_FOLDER, parent_idx)) or ""
                size_txt = model.data(model.index(idx.row(), COL_SIZE_BYTES, parent_idx)) or ""

                values["Group"] = str(group_text)
                values["File Name"] = str(name)
                values["Folder"] = str(folder)
                values["Size (Bytes)"] = str(size_txt)
            else:
                # Group row selected → no data row defaults
                pass
        except Exception:
            pass

        return values
