"""MenuController: Manages menu creation and action connections."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenuBar


class MenuController:
    """Manages main window menu creation and action connections.

    This class encapsulates all menu-related functionality including:
    - Menu structure creation
    - Action creation and organization
    - Action-to-handler connection management
    """

    def __init__(self, main_window: QMainWindow) -> None:
        """Initialize with main window reference.

        Args:
            main_window: The QMainWindow to create menus for
        """
        self.window = main_window
        self.actions: dict[str, QAction] = {}

    def setup_menus(self) -> dict[str, QAction]:
        """Create all menus and return action references.

        Returns:
            Dictionary mapping action names to QAction instances
        """
        menubar = QMenuBar(self.window)

        # File Menu
        file_menu = menubar.addMenu("File")
        self.actions["import"] = file_menu.addAction("Import CSV…")
        self.actions["export"] = file_menu.addAction("Export CSV…")
        self.actions["delete"] = file_menu.addAction("Delete Selected…")
        file_menu.addSeparator()
        self.actions["exit"] = file_menu.addAction("Exit")

        # Select Menu
        select_menu = menubar.addMenu("Select")
        self.actions["select_by"] = select_menu.addAction("Select by Field/Regex…")

        # List Menu
        list_menu = menubar.addMenu("List")
        self.actions["remove_from_list"] = list_menu.addAction("Remove from List")

        # Log Menu
        log_menu = menubar.addMenu("Log")
        self.actions["open_latest_log"] = log_menu.addAction("Open Latest Log")
        self.actions["open_latest_delete_log"] = log_menu.addAction("Open Latest Delete Log")
        log_menu.addSeparator()
        self.actions["open_log_directory"] = log_menu.addAction("Open Log Directory")
        self.actions["open_delete_log_directory"] = log_menu.addAction("Open Delete Log Directory")

        self.window.setMenuBar(menubar)

        # Store actions as window attributes for backward compatibility
        self.window.action_import = self.actions["import"]
        self.window.action_export = self.actions["export"]
        self.window.action_delete = self.actions["delete"]
        self.window.action_exit = self.actions["exit"]
        self.window.action_select_by = self.actions["select_by"]
        self.window.action_remove_from_list = self.actions["remove_from_list"]

        return self.actions

    def connect_actions(self, handlers: dict[str, Callable]) -> None:
        """Connect menu actions to their handler methods.

        Args:
            handlers: Dictionary mapping action names to handler callables
        """
        # File menu actions
        if "import" in handlers:
            self.actions["import"].triggered.connect(handlers["import"])

        if "export" in handlers:
            self.actions["export"].triggered.connect(handlers["export"])

        if "delete" in handlers:
            self.actions["delete"].triggered.connect(handlers["delete"])

        if "exit" in handlers:
            self.actions["exit"].triggered.connect(handlers["exit"])
        else:
            # Default exit behavior
            self.actions["exit"].triggered.connect(self.window.close)

        # Select menu actions
        if "select_by" in handlers:
            self.actions["select_by"].triggered.connect(handlers["select_by"])

        # List menu actions
        if "remove_from_list" in handlers:
            self.actions["remove_from_list"].triggered.connect(handlers["remove_from_list"])

        # Log menu actions
        if "open_latest_log" in handlers:
            self.actions["open_latest_log"].triggered.connect(handlers["open_latest_log"])

        if "open_latest_delete_log" in handlers:
            self.actions["open_latest_delete_log"].triggered.connect(
                handlers["open_latest_delete_log"]
            )

        if "open_log_directory" in handlers:
            self.actions["open_log_directory"].triggered.connect(handlers["open_log_directory"])

        if "open_delete_log_directory" in handlers:
            self.actions["open_delete_log_directory"].triggered.connect(
                handlers["open_delete_log_directory"]
            )

    def get_action(self, name: str) -> QAction | None:
        """Get a specific action by name.

        Args:
            name: Action name

        Returns:
            QAction instance or None if not found
        """
        return self.actions.get(name)

    def enable_action(self, name: str, enabled: bool = True) -> None:
        """Enable or disable a specific action.

        Args:
            name: Action name
            enabled: Whether to enable the action
        """
        action = self.actions.get(name)
        if action:
            action.setEnabled(enabled)

    def get_all_actions(self) -> dict[str, QAction]:
        """Get all actions.

        Returns:
            Dictionary of all actions
        """
        return self.actions.copy()
