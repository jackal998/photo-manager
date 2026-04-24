"""ContextMenuHandler: Manages context menu creation and actions."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Protocol

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu, QTreeView

from app.views.media_utils import normalize_windows_path


_SETTABLE_DECISIONS = ("delete", "keep")


class ActionHandlers(Protocol):
    """Protocol for action handler callbacks."""

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

    def set_decision(self, items: list[dict], decision: str) -> None:
        """Set the user decision (delete/keep) for file items."""
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
        self.tree = tree_view
        self.item_provider = tree_item_provider
        self.handlers = action_handlers
        self.parent = parent_widget

    def setup_context_menu(self) -> None:
        """Setup context menu policy and connect signals."""
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, point: QPoint) -> None:
        index = self.tree.indexAt(point)
        if not index.isValid():
            return

        selected_items = self.item_provider.get_selected_items()
        if not selected_items:
            return

        menu = QMenu(self.parent)

        if len(selected_items) == 1:
            self._create_single_selection_menu(menu, selected_items[0])
        else:
            self._create_multi_selection_menu(menu, selected_items)

        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _create_single_selection_menu(self, menu: QMenu, item: dict) -> None:
        if item["type"] == "file":
            # Add Select/Unselect file options
            select_file_action = menu.addAction("Select File")
            select_file_action.triggered.connect(lambda: self.handlers.select_files([item]))

            unselect_file_action = menu.addAction("Unselect File")
            unselect_file_action.triggered.connect(lambda: self.handlers.unselect_files([item]))

            # Set Action submenu
            menu.addSeparator()
            set_action_menu = menu.addMenu("Set Action")
            for dec in _SETTABLE_DECISIONS:
                a = set_action_menu.addAction(dec)
                a.triggered.connect(
                    lambda checked=False, _dec=dec, _item=item:
                        self.handlers.set_decision([_item], _dec)
                )

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
                        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
                except Exception:
                    try:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
                    except Exception:
                        pass

            open_folder_action.triggered.connect(_open_folder)

        elif item["type"] == "group":
            select_files_action = menu.addAction("Select Files")
            select_files_action.triggered.connect(lambda: self.handlers.select_files([item]))

            unselect_files_action = menu.addAction("Unselect Files")
            unselect_files_action.triggered.connect(lambda: self.handlers.unselect_files([item]))

        # Common actions for single selection
        select_action = menu.addAction("Select by Field/Regex")
        select_action.triggered.connect(lambda: self.handlers.show_select_dialog())

        remove_action = menu.addAction("Remove from List")
        remove_action.triggered.connect(lambda: self.handlers.remove_items_from_list([item]))

    def _create_multi_selection_menu(self, menu: QMenu, selected_items: list[dict]) -> None:
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
