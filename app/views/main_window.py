"""Refactored MainWindow using extracted components.

This module contains the refactored MainWindow that uses specialized controllers
and handlers while preserving all existing public interfaces for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QTreeView,
)
from loguru import logger

from app.views.components.menu_controller import MenuController
from app.views.components.selection_controller import SelectionController

# Import extracted components
from app.views.components.tree_controller import TreeController
from app.views.constants import COL_GROUP
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
        repo: Any,
        image_service: Any | None = None,
        settings: Any | None = None,
        delete_service: Any | None = None,
    ) -> None:
        """Initialize MainWindow with all services and components.

        Args:
            vm: ViewModel instance for data operations
            repo: Repository instance for CSV operations
            image_service: Image service for loading/processing images
            settings: Settings instance for configuration
            delete_service: Delete service for file deletion
        """
        super().__init__()

        # Initialize services and state
        self._initialize_services(vm, repo, image_service, settings, delete_service)

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
        repo: Any,
        image_service: Any | None,
        settings: Any | None,
        delete_service: Any | None,
    ) -> None:
        """Initialize all service dependencies.

        Args:
            vm: ViewModel instance
            repo: Repository instance
            image_service: Image service instance
            settings: Settings instance
            delete_service: Delete service instance
        """
        self._vm = vm
        self._repo = repo
        self._img = image_service
        self._settings = settings
        self._deleter = delete_service

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

        # Initialize selection controller
        self.selection_controller = SelectionController(
            self.tree_controller, self._vm, self.status_reporter
        )

        # Initialize file operations handler
        self.file_operations = FileOperationsHandler(
            vm=self._vm,
            repo=self._repo,
            delete_service=self._deleter,
            settings=self._settings,
            parent_widget=self,
            ui_updater=self.ui_updater,
            status_reporter=self.status_reporter,
        )

        # Tree data provider for dialog handler
        self.tree_data_provider = TreeDataProviderImpl(self.tree, self.tree_controller)

        # Initialize dialog handler
        self.dialog_handler = DialogHandler(
            parent_widget=self,
            tree_data_provider=self.tree_data_provider,
            regex_handler=self._apply_select_regex,
        )

        # Action handlers for context menu
        self.action_handlers = ActionHandlersImpl(
            file_operations=self.file_operations,
            selection_controller=self.selection_controller,
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
        self.setWindowTitle("Photo Manager - M1")

        # Setup tree properties
        self.tree_controller.setup_tree_properties()

        # Create layout sections
        center_widget, center_layout = self.layout_manager.create_tree_section()
        center_layout.addWidget(self.tree)

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
            "import": self.on_import_csv,
            "export": self.on_export_csv,
            "delete": self.on_delete_selected,
            "select_by": self.on_open_select_dialog,
            "remove_from_list": self._remove_from_list_toolbar,
            "exit": self.close,
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

    def on_import_csv(self) -> None:
        """Handle CSV import action."""
        self.file_operations.import_csv()

    def on_export_csv(self) -> None:
        """Handle CSV export action."""
        self.file_operations.export_csv()

    def on_delete_selected(self) -> None:
        """Handle delete selected action."""
        selected_paths = self.selection_controller.gather_checked_paths()
        self.file_operations.delete_selected_files(selected_paths)

    def on_open_select_dialog(self) -> None:
        """Handle open select dialog action."""
        self.dialog_handler.show_select_dialog()

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
            name_index = model.index(idx.row(), 2, idx.parent())  # COL_NAME
            folder_index = model.index(idx.row(), 3, idx.parent())  # COL_FOLDER
            name = model.data(name_index)
            folder = model.data(folder_index)
            path = model.data(name_index, 32)  # PATH_ROLE
            if not path:
                if not folder or not name:
                    return
                path = str(Path(folder) / name)
            self._preview.show_single(path)
        else:
            # Group level selected -> grid thumbnails
            group_items: list[tuple[str, str, str, str]] = []
            parent_item = model.itemFromIndex(model.index(idx.row(), COL_GROUP))
            if parent_item is not None:
                rows = parent_item.rowCount()
                for r in range(rows):
                    name_item = parent_item.child(r, 2)  # COL_NAME
                    folder_item = parent_item.child(r, 3)  # COL_FOLDER
                    name = model.itemFromIndex(name_item.index()).text() if name_item else ""
                    folder = model.itemFromIndex(folder_item.index()).text() if folder_item else ""
                    size_txt = (
                        model.itemFromIndex(
                            parent_item.child(r, 4).index()
                        ).text()  # COL_SIZE_BYTES
                        if parent_item.child(r, 4)
                        else ""
                    )
                    if name and folder:
                        p = name_item.data(32) if name_item else None  # PATH_ROLE
                        if not p:
                            p = str(Path(folder) / name)
                        group_items.append((p, name, folder, size_txt))
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

    # PRESERVED: Close event handler

    def closeEvent(self, event) -> None:
        """Handle application close event to prompt for source file update.

        Args:
            event: Close event
        """
        try:
            # Check if there are any changes that might need saving
            source_path = self._vm.get_source_csv_path()
            if source_path and self._vm.groups:
                # Ask user if they want to update the source file
                reply = QMessageBox.question(
                    self,
                    "Update Source File",
                    f"Do you want to update the source CSV file with the current list?\n\n{source_path}",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.No,
                )

                if reply == QMessageBox.Yes:
                    try:
                        self._vm.export_csv(source_path)
                        logger.info("Source file updated on exit: {}", source_path)
                        QMessageBox.information(
                            self, "File Updated", "Source file has been updated successfully."
                        )
                    except Exception as ex:
                        logger.error("Failed to update source file on exit: {}", ex)
                        QMessageBox.warning(
                            self, "Update Failed", f"Failed to update source file:\n{str(ex)}"
                        )
                        event.ignore()
                        return
                elif reply == QMessageBox.Cancel:
                    event.ignore()
                    return
                # If No, continue with closing without saving

            # Accept the close event
            event.accept()

        except Exception as ex:
            logger.error("Close event handler failed: {}", ex)
            # In case of error, just accept the close event
            event.accept()

    # Private methods

    def _remove_from_list_toolbar(self) -> None:
        """Handle remove from list toolbar action."""
        checked_paths = self.selection_controller.gather_checked_paths()
        highlighted_items = self.tree_controller.get_selected_items()
        self.file_operations.remove_from_list_toolbar(checked_paths, highlighted_items)

    def _apply_select_regex(self, field: str, pattern: str, make_checked: bool) -> None:
        """Apply regex selection to files.

        Args:
            field: Field name to match against
            pattern: Regex pattern
            make_checked: Whether to check or uncheck matches
        """
        self.selection_controller.apply_regex_selection(field, pattern, make_checked, self)

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

    # PRESERVED: Legacy methods for backward compatibility

    def on_edit_rules(self) -> None:  # pragma: no cover
        """Legacy method preserved for compatibility."""
        from app.views.dialogs.rules_dialog import RulesDialog

        dlg = RulesDialog(self)
        dlg.exec()

    def on_edit_filters(self) -> None:  # pragma: no cover
        """Legacy method preserved for compatibility."""
        from app.views.dialogs.filters_dialog import FiltersDialog

        dlg = FiltersDialog(self)
        dlg.exec()


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
        selection_controller: SelectionController,
        dialog_handler: DialogHandler,
    ):
        self.file_ops = file_operations
        self.selection = selection_controller
        self.dialog = dialog_handler

    def delete_files(self, items: list[dict]) -> None:
        """Delete files from items list."""
        self.file_ops.delete_files(items)

    def select_files(self, items: list[dict]) -> None:
        """Select files from items list."""
        self.selection.select_files(items)

    def unselect_files(self, items: list[dict]) -> None:
        """Unselect files from items list."""
        self.selection.unselect_files(items)

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove items from list."""
        self.file_ops.remove_items_from_list(items)

    def show_select_dialog(self) -> None:
        """Show select by field/regex dialog."""
        self.dialog.show_select_dialog()
