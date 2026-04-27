"""ContextMenuHandler: Manages context menu creation and actions."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Protocol

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu, QTreeView

from app.views.constants import SETTABLE_DECISIONS as _SETTABLE_DECISIONS
from app.views.media_utils import normalize_windows_path


class ActionHandlers(Protocol):
    """Protocol for action handler callbacks."""

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove items from list."""
        ...

    def show_action_dialog(self, clicked_col: int | None = None) -> None:
        """Show set action by field/regex dialog."""
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
    """Manages context menu creation and actions."""

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

        clicked_col: int | None = index.column()
        selected_items = self.item_provider.get_selected_items()
        if not selected_items:
            return

        menu = QMenu(self.parent)

        if len(selected_items) == 1:
            self._create_single_selection_menu(menu, selected_items[0], clicked_col)
        else:
            self._create_multi_selection_menu(menu, selected_items)

        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _create_single_selection_menu(
        self, menu: QMenu, item: dict, clicked_col: int | None = None
    ) -> None:
        if item["type"] == "file":
            # Set Action submenu
            set_action_menu = menu.addMenu("Set Action")
            for label, value in _SETTABLE_DECISIONS:
                a = set_action_menu.addAction(label)
                a.triggered.connect(
                    lambda checked=False, _v=value, _item=item:
                        self.handlers.set_decision([_item], _v)
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

        # Common actions for single selection
        menu.addSeparator()
        action_dialog_action = menu.addAction("Set Action by Field/Regex…")
        action_dialog_action.triggered.connect(
            lambda: self.handlers.show_action_dialog(clicked_col=clicked_col)
        )

        remove_action = menu.addAction("Remove from List")
        remove_action.triggered.connect(lambda: self.handlers.remove_items_from_list([item]))

    def _create_multi_selection_menu(self, menu: QMenu, selected_items: list[dict]) -> None:
        # Set Action submenu — only applies to file-type items
        set_action_menu = menu.addMenu("Set Action")
        file_items = [it for it in selected_items if it.get("type") == "file"]
        for label, value in _SETTABLE_DECISIONS:
            a = set_action_menu.addAction(label)
            a.triggered.connect(
                lambda checked=False, _v=value, _items=file_items:
                    self.handlers.set_decision(_items, _v)
            )

        menu.addSeparator()
        remove_action = menu.addAction("Remove from List")
        remove_action.triggered.connect(
            lambda: self.handlers.remove_items_from_list(selected_items)
        )
