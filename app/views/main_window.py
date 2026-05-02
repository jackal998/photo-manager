"""Refactored MainWindow using extracted components.

This module contains the refactored MainWindow that uses specialized controllers
and handlers while preserving all existing public interfaces for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QTreeView,
)
from loguru import logger

from app.views.components.menu_controller import MenuController

# Import extracted components
from app.views.components.tree_controller import TreeController
from app.views.constants import COL_CREATION_DATE, COL_FOLDER, COL_GROUP, COL_NAME, COL_SHOT_DATE, COL_SIZE_BYTES
from app.views.handlers.context_menu import ContextMenuHandler
from app.views.handlers.dialog_handler import DialogHandler
from app.views.handlers.file_operations import FileOperationsHandler
from app.views.image_tasks import ImageTaskRunner
from app.views.layout.layout_manager import LayoutManager
from app.views.preview_pane import PreviewPane


class MainWindow(QMainWindow):
    """Main application window with refactored architecture.

    This class maintains all existing public interfaces while using extracted
    components for better maintainability and testability.
    """

    # PRESERVED: Critical signal for ImageTaskRunner
    imageLoaded = Signal(str, str, object)  # token, path, QImage

    def __init__(
        self,
        vm: Any,
        image_service: Any | None = None,
        settings: Any | None = None,
    ) -> None:
        super().__init__()
        self._initialize_services(vm, image_service, settings)

        # Setup components
        self._setup_components()

        # Setup UI
        self._setup_ui()

        # Connect signals
        self._connect_signals()

        # Setup window properties
        self._setup_window_properties()

    def _initialize_services(
        self,
        vm: Any,
        image_service: Any | None,
        settings: Any | None,
    ) -> None:
        self._vm = vm
        self._img = image_service
        self._settings = settings

        # Initialize thumbnail size from settings
        self._thumb_size: int = 512
        if self._settings is not None:
            try:
                self._thumb_size = int(self._settings.get("thumbnail_size", 512) or 512)
            except Exception:
                self._thumb_size = 512

    def _setup_components(self) -> None:
        """Setup all extracted components and controllers."""
        # Create tree view first
        self.tree = QTreeView()

        # Initialize controllers
        self.tree_controller = TreeController(self.tree)
        self.menu_controller = MenuController(self)
        self.layout_manager = LayoutManager(self)

        # Status reporter and UI updater implementations
        self.status_reporter = StatusReporterImpl(self)
        self.ui_updater = UIUpdaterImpl(self)

        # Initialize file operations handler
        self.file_operations = FileOperationsHandler(
            vm=self._vm,
            settings=self._settings,
            parent_widget=self,
            ui_updater=self.ui_updater,
            status_reporter=self.status_reporter,
            checked_paths_provider=None,
            highlighted_items_provider=self.tree_controller,
        )

        # Tree data provider for dialog handler
        self.tree_data_provider = TreeDataProviderImpl(self.tree, self.tree_controller)

        # Initialize dialog handler
        self.dialog_handler = DialogHandler(
            parent_widget=self,
            tree_data_provider=self.tree_data_provider,
            action_handler=self._apply_action_by_regex,
        )

        # Action handlers for context menu
        self.action_handlers = ActionHandlersImpl(
            file_operations=self.file_operations,
            dialog_handler=self.dialog_handler,
        )

        # Initialize context menu handler
        self.context_menu_handler = ContextMenuHandler(
            tree_view=self.tree,
            tree_item_provider=self.tree_controller,
            action_handlers=self.action_handlers,
            parent_widget=self,
        )

    def _setup_ui(self) -> None:
        """Setup the main UI components and layout."""
        self.setWindowTitle("Photo Manager")

        # Setup tree properties
        self.tree_controller.setup_tree_properties()

        # Create layout sections
        center_widget, center_layout = self.layout_manager.create_tree_section()
        # First-run hint — visible until the first manifest loads. Once a
        # manifest is loaded (even if it produces zero groups), the user has
        # discovered the menu and the label is hidden permanently (#42).
        self._empty_state_label = QLabel(
            "No manifest loaded.\n\nFile → Scan Sources… to begin."
        )
        self._empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state_label.setStyleSheet(
            "color: #888; font-size: 14px; padding: 40px;"
        )
        center_layout.addWidget(self._empty_state_label)
        center_layout.addWidget(self.tree)
        self.tree.setVisible(False)

        right_widget, right_layout = self.layout_manager.create_preview_section()

        # Create image task runner and preview pane
        self._runner = ImageTaskRunner(service=self._img, receiver=self)
        self._preview = PreviewPane(right_widget, self._runner, thumb_size=self._thumb_size)
        right_layout.addWidget(self._preview)

        # Setup main layout with splitter
        central = self.layout_manager.setup_main_layout(center_widget, right_widget)
        self.setCentralWidget(central)

        # Connect splitter signals
        self.layout_manager.connect_splitter_signals(self._preview.refit)

        # Setup initial window size
        self.layout_manager.setup_initial_window_size()

        # Setup menus
        self.menu_controller.setup_menus()

        # Setup context menu
        self.context_menu_handler.setup_context_menu()

    def _connect_signals(self) -> None:
        """Connect all signal/slot relationships."""
        # Menu action handlers
        handlers = {
            "scan_sources": self.on_scan_sources,
            "open_manifest": self.on_open_manifest,
            "save_manifest": self.on_save_manifest,
            "execute_action": self.on_execute_action,
            "action_by_regex": self.on_open_action_dialog,
            "remove_from_list": self._remove_from_list_toolbar,
            "exit": self.close,
            "open_latest_log": self._open_latest_log,
            "open_latest_delete_log": self._open_latest_delete_log,
            "open_log_directory": self._open_log_directory,
            "open_delete_log_directory": self._open_delete_log_directory,
        }
        self.menu_controller.connect_actions(handlers)

        # Tree header click handler
        self.tree_controller.setup_header_behavior(self._on_header_clicked)

        # Image loading signal
        self.imageLoaded.connect(self._on_image_loaded)

    def _setup_window_properties(self) -> None:
        """Setup window properties and status bar."""
        # Status bar
        self.statusBar().showMessage("Ready", 3000)

    # PRESERVED: All public methods with exact signatures

    def refresh_tree(self, groups: list) -> None:
        """Refresh tree view with new groups data.

        Args:
            groups: List of group objects to display
        """
        # First manifest load — hide the first-run hint and reveal the tree.
        # Stays hidden afterwards even if a later load produces zero groups,
        # because the user has clearly already discovered the entry point.
        if self._empty_state_label.isVisible():
            self._empty_state_label.setVisible(False)
            self.tree.setVisible(True)

        self.tree_controller.refresh_model(groups)

        # Reconnect selection handler after model reset
        self.tree_controller.reconnect_selection_handler(self.on_tree_selection_changed)

        # Adjust splitter for tree content
        self.layout_manager.adjust_splitter_for_tree(self.tree_controller.calculate_tree_width)

    def show_group_counts(self, group_count: int) -> None:
        """Show group counts (preserved for backward compatibility).

        Args:
            group_count: Number of groups
        """
        # No-op: groups sidebar removed; keep method to avoid breaking callers
        pass

    def show_groups_summary(self, groups: list) -> None:
        """Show groups summary (preserved for backward compatibility).

        Args:
            groups: List of groups
        """
        # No-op: groups sidebar removed; keep method to avoid breaking callers
        pass

    # PRESERVED: Menu action handlers

    def on_scan_sources(self) -> None:
        """Open the Scan Sources dialog."""
        from app.views.dialogs.scan_dialog import ScanDialog
        dlg = ScanDialog(
            settings=self._settings,
            on_scan_complete=self._load_manifest_from_path,
            parent=self,
        )
        dlg.exec()

    def _load_manifest_from_path(self, manifest_path: str) -> None:
        """Load a manifest directly (called after scan completes or from Open Manifest)."""
        import sqlite3

        from infrastructure.manifest_repository import ManifestRepository
        try:
            self._vm.load_from_repo(ManifestRepository(), manifest_path)
            self.file_operations._manifest_path = manifest_path
            self.show_groups_summary(self._vm.groups)
            self.refresh_tree(self._vm.groups)
            for action in ("save_manifest", "execute_action", "remove_from_list"):
                try:
                    self.menu_controller.enable_action(action, True)
                except AttributeError:
                    pass
            n = self._vm.group_count
            # Surface isolated files in the status bar so users whose scan
            # produced zero near-duplicate groups don't see an empty review
            # pane with no explanation. Isolated = total manifest rows
            # minus rows that ended up in any group.
            isolated = 0
            try:
                with sqlite3.connect(manifest_path) as conn:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM migration_manifest"
                    ).fetchone()[0] or 0
                grouped = sum(len(g.items) for g in self._vm.groups)
                isolated = max(0, total - grouped)
            except (sqlite3.Error, OSError):
                pass
            parts = [f"{n} group(s)"]
            if isolated:
                parts.append(f"{isolated:,} isolated file(s)")
            self.statusBar().showMessage(
                f"Loaded manifest: {', '.join(parts)}", 10000
            )
        except Exception as exc:
            QMessageBox.critical(self, "Load Manifest Error", str(exc))

    def on_open_manifest(self) -> None:
        """Handle Open Manifest action."""
        self.file_operations.import_manifest()

    def on_save_manifest(self) -> None:
        """Handle Save Manifest Decisions action."""
        self.file_operations.save_manifest_decisions()

    def on_execute_action(self) -> None:
        """Handle Execute Action — open review dialog and run planned operations."""
        self.file_operations.execute_action()

    def on_open_action_dialog(self) -> None:
        """Handle open Set Action by Field/Regex dialog."""
        self.dialog_handler.show_action_dialog()

    # PRESERVED: Tree selection change handler

    def on_tree_selection_changed(self, *_: Any) -> None:
        """Handle tree selection changes for preview updates.

        Args:
            *_: Selection change arguments (ignored)
        """
        # Delegate to existing preview logic using tree controller
        indexes = self.tree.selectionModel().selectedRows()
        if not indexes:
            return

        idx = indexes[0]
        view_model = self.tree.model()
        src_model = self.tree_controller.model
        proxy = self.tree_controller.proxy

        if proxy is not None and hasattr(proxy, "mapToSource"):
            src_idx = proxy.mapToSource(idx)
            model = src_model
            idx = src_idx
        else:
            model = view_model

        # Determine if group or child
        if idx.parent().isValid():
            # Child row selected -> single preview
            name_index = model.index(idx.row(), COL_NAME, idx.parent())
            folder_index = model.index(idx.row(), COL_FOLDER, idx.parent())
            name = model.data(name_index)
            folder = model.data(folder_index)
            path = model.data(name_index, 32)  # PATH_ROLE
            if not path:
                if not folder or not name:
                    return
                path = str(Path(folder) / name)
            # Optional date/size info for single preview header
            try:
                size_index = model.index(idx.row(), COL_SIZE_BYTES, idx.parent())
                creation_index = model.index(idx.row(), COL_CREATION_DATE, idx.parent())
                shot_index = model.index(idx.row(), COL_SHOT_DATE, idx.parent())
                size_txt = model.data(size_index) or ""
                creation_txt = model.data(creation_index) or ""
                shot_txt = model.data(shot_index) or ""
                self._preview.show_single(
                    path,
                    {
                        "name": name,
                        "folder": folder,
                        "size": size_txt,
                        "creation": creation_txt,
                        "shot": shot_txt,
                    },
                )
            except Exception:
                self._preview.show_single(path)
        else:
            # Group level selected -> grid thumbnails
            group_items: list[tuple[str, str, str, str, str, str]] = []
            parent_item = model.itemFromIndex(model.index(idx.row(), COL_GROUP))
            if parent_item is not None:
                rows = parent_item.rowCount()
                for r in range(rows):
                    name_item = parent_item.child(r, COL_NAME)
                    folder_item = parent_item.child(r, COL_FOLDER)
                    name = model.itemFromIndex(name_item.index()).text() if name_item else ""
                    folder = model.itemFromIndex(folder_item.index()).text() if folder_item else ""
                    size_txt = (
                        model.itemFromIndex(
                            parent_item.child(r, COL_SIZE_BYTES).index()
                        ).text()
                        if parent_item.child(r, COL_SIZE_BYTES)
                        else ""
                    )
                    creation_txt = (
                        model.itemFromIndex(parent_item.child(r, COL_CREATION_DATE).index()).text()
                        if parent_item.child(r, COL_CREATION_DATE)
                        else ""
                    )
                    shot_txt = (
                        model.itemFromIndex(parent_item.child(r, COL_SHOT_DATE).index()).text()
                        if parent_item.child(r, COL_SHOT_DATE)
                        else ""
                    )
                    if name and folder:
                        p = name_item.data(32) if name_item else None  # PATH_ROLE
                        if not p:
                            p = str(Path(folder) / name)
                        group_items.append((p, name, folder, size_txt, creation_txt, shot_txt))
            self._preview.show_grid(group_items)
            # Request autoplay for all videos after loading tiles
            try:
                self._preview.autoplay_all_videos_when_ready()
            except Exception:
                pass

    # PRESERVED: Image loading slot

    def _on_image_loaded(self, token: str, path: str, image: Any) -> None:
        """Handle image loading completion.

        Args:
            token: Image loading token
            path: Image file path
            image: Loaded image object
        """
        self._preview.on_image_loaded(token, path, image)

    # Private methods

    def _remove_from_list_toolbar(self) -> None:
        """Handle remove from list toolbar action."""
        highlighted_items = self.tree_controller.get_selected_items()
        self.file_operations.remove_from_list_toolbar(highlighted_items)

    def _apply_action_by_regex(self, field: str, pattern: str, action_value: str) -> None:
        """Apply an action to all files matching field/regex from the ActionDialog."""
        self.file_operations.set_decision_by_regex(field, pattern, action_value)

    def _on_header_clicked(self, logical_index: int) -> None:
        """Handle tree header clicks to maintain sort state.

        Args:
            logical_index: Clicked column index
        """
        try:
            current_order = self.tree.header().sortIndicatorOrder()
            self.tree_controller.update_sort_state(logical_index, current_order)
            logger.debug("Sort state updated - Column: {}, Order: {}", logical_index, current_order)
        except Exception as e:
            logger.error("Failed to track header click: {}", e)

    def _open_latest_log(self) -> None:
        """Open the latest log file."""
        from infrastructure.logging import open_latest_log

        if not open_latest_log():
            QMessageBox.warning(
                self, "Log File Not Found", "No log files found in the log directory."
            )

    def _open_latest_delete_log(self) -> None:
        """Open the latest delete log file."""
        from infrastructure.logging import open_latest_delete_log

        if not open_latest_delete_log():
            QMessageBox.warning(
                self,
                "Delete Log Not Found",
                "No delete log files found in the delete log directory.",
            )

    def _open_log_directory(self) -> None:
        """Open the log directory in file explorer."""
        from infrastructure.logging import open_log_directory

        if not open_log_directory():
            QMessageBox.warning(
                self, "Log Directory Not Found", "Log directory could not be opened."
            )

    def _open_delete_log_directory(self) -> None:
        """Open the delete log directory in file explorer."""
        from infrastructure.logging import open_delete_log_directory

        if not open_delete_log_directory():
            QMessageBox.warning(
                self, "Delete Log Directory Not Found", "Delete log directory could not be opened."
            )


# Helper implementation classes


class StatusReporterImpl:
    """Implementation of StatusReporter protocol."""

    def __init__(self, main_window: QMainWindow):
        self.window = main_window

    def show_status(self, message: str, timeout: int = 3000) -> None:
        """Show status message in status bar."""
        self.window.statusBar().showMessage(message, timeout)


class UIUpdaterImpl:
    """Implementation of UIUpdateCallback protocol."""

    def __init__(self, main_window):
        self.window = main_window

    def refresh_tree(self, groups: list) -> None:
        """Refresh tree view."""
        self.window.refresh_tree(groups)

    def show_group_counts(self, count: int) -> None:
        """Show group counts (legacy)."""
        self.window.show_group_counts(count)

    def show_groups_summary(self, groups: list) -> None:
        """Show groups summary (legacy)."""
        self.window.show_groups_summary(groups)


class TreeDataProviderImpl:
    """Implementation of TreeDataProvider protocol."""

    def __init__(self, tree_view: QTreeView, tree_controller: TreeController):
        self.tree = tree_view
        self.controller = tree_controller

    def get_selection_model(self):
        """Get tree selection model."""
        return self.tree.selectionModel()

    def get_view_model(self):
        """Get tree view model."""
        return self.tree.model()

    def get_source_model(self):
        """Get source model."""
        return self.controller.model

    def get_proxy_model(self):
        """Get proxy model."""
        return self.controller.proxy


class ActionHandlersImpl:
    """Implementation of ActionHandlers protocol for context menu."""

    def __init__(
        self,
        file_operations: FileOperationsHandler,
        dialog_handler: DialogHandler,
    ):
        self.file_ops = file_operations
        self.dialog = dialog_handler

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove items from list."""
        self.file_ops.remove_items_from_list(items)

    def show_action_dialog(self, clicked_col: int | None = None) -> None:
        """Show set action by field/regex dialog."""
        self.dialog.show_action_dialog(clicked_col=clicked_col)

    def set_decision(self, items: list[dict], decision: str) -> None:
        """Set user decision (delete/keep) for file items."""
        self.file_ops.set_decision(items, decision)
