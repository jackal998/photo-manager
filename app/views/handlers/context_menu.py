"""ContextMenuHandler: Manages context menu creation and actions."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Protocol

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu, QTreeView

from app.views.media_utils import normalize_windows_path


class ActionHandlers(Protocol):
    """Protocol for action handler callbacks."""

    def delete_files(self, items: list[dict]) -> None:
        """Delete files from items list."""
        ...

    def select_files(self, items: list[dict]) -> None:
        """Select files from items list."""
        ...

    def unselect_files(self, items: list[dict]) -> None:
        """Unselect files from items list."""
        ...

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove items from list."""
        ...

    def show_select_dialog(self) -> None:
        """Show select by field/regex dialog."""
        ...


class TreeItemProvider(Protocol):
    """Protocol for tree item provider."""

    def get_selected_items(self) -> list[dict]:
        """Get currently selected items."""
        ...


class ContextMenuHandler:
    """Manages context menu creation and actions.

    This class encapsulates all context menu functionality including:
    - Context menu setup and policy
    - Dynamic menu creation based on selection
    - Action routing to appropriate handlers
    """

    def __init__(
        self,
        tree_view: QTreeView,
        tree_item_provider: TreeItemProvider,
        action_handlers: ActionHandlers,
        parent_widget: Any,
    ) -> None:
        """Initialize with tree view and action handlers.

        Args:
            tree_view: The QTreeView to manage context menus for
            tree_item_provider: Provider for getting selected items
            action_handlers: Handler for context menu actions
            parent_widget: Parent widget for menu creation
        """
        self.tree = tree_view
        self.item_provider = tree_item_provider
        self.handlers = action_handlers
        self.parent = parent_widget

    def setup_context_menu(self) -> None:
        """Setup context menu policy and connect signals."""
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, point: QPoint) -> None:
        """Handle context menu request at the given point.

        Args:
            point: Point where context menu was requested
        """
        index = self.tree.indexAt(point)
        if not index.isValid():
            return  # Only show menu for valid rows

        # Get all selected items
        selected_items = self.item_provider.get_selected_items()
        if not selected_items:
            return

        menu = QMenu(self.parent)

        # Analyze selection
        files_only = all(item["type"] == "file" for item in selected_items)

        if len(selected_items) == 1:
            self._create_single_selection_menu(menu, selected_items[0])
        else:
            self._create_multi_selection_menu(menu, selected_items, files_only)

        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _create_single_selection_menu(self, menu: QMenu, item: dict) -> None:
        """Create context menu for single item selection.

        Args:
            menu: QMenu to populate
            item: Selected item dictionary
        """
        if item["type"] == "file":
            # File-specific actions
            delete_action = menu.addAction("Delete File")
            delete_action.triggered.connect(lambda: self.handlers.delete_files([item]))

            # Add Select/Unselect file options
            select_file_action = menu.addAction("Select File")
            select_file_action.triggered.connect(lambda: self.handlers.select_files([item]))

            unselect_file_action = menu.addAction("Unselect File")
            unselect_file_action.triggered.connect(lambda: self.handlers.unselect_files([item]))

            # Open Folder action
            open_folder_action = menu.addAction("Open Folder")

            def _open_folder(checked: bool = False, path: str = item.get("path", "")) -> None:
                try:
                    if not path:
                        return
                    norm_path = normalize_windows_path(path)
                    folder = os.path.dirname(norm_path) or norm_path
                    if not folder:
                        return
                    # Use Windows Explorer to open folder and select the file if possible
                    if os.name == "nt":
                        try:
                            if os.path.exists(norm_path):
                                subprocess.Popen(["explorer", "/select,", norm_path])
                            elif os.path.isdir(folder):
                                subprocess.Popen(["explorer", folder])
                            else:
                                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
                        except Exception:
                            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
                    else:
                        # Cross-platform fallback
                        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
                except Exception:
                    try:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
                    except Exception:
                        pass

            open_folder_action.triggered.connect(_open_folder)

        elif item["type"] == "group":
            # Group-specific actions
            select_files_action = menu.addAction("Select Files")
            select_files_action.triggered.connect(lambda: self.handlers.select_files([item]))

            unselect_files_action = menu.addAction("Unselect Files")
            unselect_files_action.triggered.connect(lambda: self.handlers.unselect_files([item]))

        # Common actions for single selection
        select_action = menu.addAction("Select by Field/Regex")
        select_action.triggered.connect(lambda: self.handlers.show_select_dialog())

        remove_action = menu.addAction("Remove from List")
        remove_action.triggered.connect(lambda: self.handlers.remove_items_from_list([item]))

    def _create_multi_selection_menu(
        self, menu: QMenu, selected_items: list[dict], files_only: bool
    ) -> None:
        """Create context menu for multiple item selection.

        Args:
            menu: QMenu to populate
            selected_items: List of selected items
            files_only: Whether selection contains only files
        """
        if files_only:
            # Only files selected - include delete option
            delete_action = menu.addAction("Delete Files")
            delete_action.triggered.connect(lambda: self.handlers.delete_files(selected_items))

        # Common multi-selection actions
        select_files_action = menu.addAction("Select Files")
        select_files_action.triggered.connect(lambda: self.handlers.select_files(selected_items))

        unselect_files_action = menu.addAction("Unselect Files")
        unselect_files_action.triggered.connect(
            lambda: self.handlers.unselect_files(selected_items)
        )

        remove_action = menu.addAction("Remove from List")
        remove_action.triggered.connect(
            lambda: self.handlers.remove_items_from_list(selected_items)
        )
