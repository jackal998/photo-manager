"""MenuController: Manages menu creation and action connections."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenuBar


class MenuController:
    """Manages main window menu creation and action connections."""

    def __init__(self, main_window: QMainWindow) -> None:
        self.window = main_window
        self.actions: dict[str, QAction] = {}

    def setup_menus(self) -> dict[str, QAction]:
        """Create all menus and return action references."""
        menubar = QMenuBar(self.window)

        # File Menu
        file_menu = menubar.addMenu("File")
        self.actions["scan_sources"] = file_menu.addAction("Scan Sources…")
        file_menu.addSeparator()
        self.actions["open_manifest"] = file_menu.addAction("Open Manifest…")
        self.actions["save_manifest"] = file_menu.addAction("Save Manifest Decisions…")
        self.actions["save_manifest"].setEnabled(False)
        file_menu.addSeparator()
        self.actions["exit"] = file_menu.addAction("Exit")

        # Action Menu
        action_menu = menubar.addMenu("Action")
        self.actions["action_by_regex"] = action_menu.addAction("Set Action by Field/Regex…")
        action_menu.addSeparator()
        self.actions["execute_action"] = action_menu.addAction("Execute Action…")
        self.actions["execute_action"].setEnabled(False)

        # List Menu
        list_menu = menubar.addMenu("List")
        self.actions["remove_from_list"] = list_menu.addAction("Remove from List")
        # Disabled until a manifest loads — gives the user a visible-but-greyed
        # entry instead of a menu that appears empty / no-op before any data is
        # present. Re-enabled in MainWindow._load_manifest_from_path.
        self.actions["remove_from_list"].setEnabled(False)

        # Log Menu
        log_menu = menubar.addMenu("Log")
        self.actions["open_latest_log"] = log_menu.addAction("Open Latest Log")
        self.actions["open_latest_delete_log"] = log_menu.addAction("Open Latest Delete Log")
        log_menu.addSeparator()
        self.actions["open_log_directory"] = log_menu.addAction("Open Log Directory")
        self.actions["open_delete_log_directory"] = log_menu.addAction("Open Delete Log Directory")

        self.window.setMenuBar(menubar)

        # Store actions as window attributes for backward compatibility
        self.window.action_exit = self.actions["exit"]
        self.window.action_remove_from_list = self.actions["remove_from_list"]

        return self.actions

    def connect_actions(self, handlers: dict[str, Callable]) -> None:
        """Connect menu actions to their handler methods."""
        for name, action in self.actions.items():
            if name in handlers:
                action.triggered.connect(handlers[name])
            elif name == "exit":
                action.triggered.connect(self.window.close)

    def get_action(self, name: str) -> QAction | None:
        return self.actions.get(name)

    def enable_action(self, name: str, enabled: bool = True) -> None:
        action = self.actions.get(name)
        if action:
            action.setEnabled(enabled)

    def get_all_actions(self) -> dict[str, QAction]:
        return self.actions.copy()
