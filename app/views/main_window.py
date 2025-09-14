from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.views.constants import (
    COL_FOLDER,
    COL_GROUP,
    COL_NAME,
    COL_SEL,
    COL_SIZE_BYTES,
    NUM_COLUMNS,
    PATH_ROLE,
    SORT_ROLE,
)
from app.views.image_tasks import ImageTaskRunner
from app.views.preview_pane import PreviewPane
from app.views.selection_service import apply_select_regex
from app.views.tree_model_builder import build_model


class MainWindow(QMainWindow):
    imageLoaded = Signal(str, str, object)  # token, path, QImage

    def __init__(
        self,
        vm: Any,
        repo: Any,
        image_service: Any | None = None,
        settings: Any | None = None,
        delete_service: Any | None = None,
    ) -> None:
        super().__init__()
        self._vm = vm
        self._repo = repo
        self._img = image_service
        self._settings = settings
        self._deleter = delete_service
        self._thumb_size: int = 512
        if self._settings is not None:
            try:
                self._thumb_size = int(self._settings.get("thumbnail_size", 512) or 512)
            except Exception:
                self._thumb_size = 512

        self.setWindowTitle("Photo Manager - M1")
        central = QWidget(self)
        root = QHBoxLayout(central)

        # Menu
        menubar = QMenuBar(self)
        file_menu = menubar.addMenu("File")
        self.action_import = file_menu.addAction("Import CSV…")
        self.action_export = file_menu.addAction("Export CSV…")
        self.action_delete = file_menu.addAction("Delete Selected…")
        file_menu.addSeparator()
        self.action_exit = file_menu.addAction("Exit")
        select_menu = menubar.addMenu("Select")
        self.action_select_by = select_menu.addAction("Select by Field/Regex…")
        list_menu = menubar.addMenu("List")
        self.action_remove_from_list = list_menu.addAction("Remove from List")
        self.setMenuBar(menubar)

        self.action_import.triggered.connect(self.on_import_csv)
        self.action_export.triggered.connect(self.on_export_csv)
        self.action_exit.triggered.connect(self.close)
        self.action_delete.triggered.connect(self.on_delete_selected)
        self.action_select_by.triggered.connect(self.on_open_select_dialog)
        self.action_remove_from_list.triggered.connect(self._remove_from_list_toolbar)

        # Status bar
        self.statusBar().showMessage("Ready", 3000)

        # Center: tree view (groups/items)
        center_widget = QWidget()
        center = QVBoxLayout(center_widget)
        self.tree = QTreeView()
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        # Enable multi-selection
        self.tree.setSelectionMode(QTreeView.ExtendedSelection)
        center.addWidget(self.tree)

        # Right: preview area (single image or grid) encapsulated in PreviewPane
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        self._runner = ImageTaskRunner(service=self._img, receiver=self)
        self._preview = PreviewPane(right_widget, self._runner, thumb_size=self._thumb_size)
        right.addWidget(self._preview)

        # Splitter to allow resizable boundary
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        self._splitter = splitter
        # Refit on splitter moves (handles tree/preview border drags)
        try:
            self._splitter.splitterMoved.connect(lambda *_: self._preview.refit())
        except Exception:
            pass

        # Root layout: splitter only
        root.addWidget(splitter)

        self.setCentralWidget(central)

        # Image loading signal connection
        self.imageLoaded.connect(self._on_image_loaded)

        # Selection handling will be connected after a model is set in refresh_tree
        # Tree header behaviors: draggable and interactive resize
        try:
            header = self.tree.header()
            header.setSectionsMovable(True)
            header.setStretchLastSection(False)
            header.setSectionsClickable(True)
            header.setSectionResizeMode(QHeaderView.Interactive)
        except Exception:
            pass

        # Default window size: half of available screen in both width and height
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                rect = screen.availableGeometry()
                self.resize(int(rect.width() * 0.5), int(rect.height() * 0.5))
        except Exception:
            pass

        # PreviewPane handles its own resize and event filters

        self._setup_context_menu()

    def _setup_context_menu(self):
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, point):
        index = self.tree.indexAt(point)
        if not index.isValid():
            return  # Only show menu for valid rows

        # Get all selected items
        selected_items = self._get_selected_items()
        if not selected_items:
            return

        menu = QMenu(self)

        # Analyze selection
        files_only = all(item["type"] == "file" for item in selected_items)

        if len(selected_items) == 1:
            # Single selection - use original logic
            item = selected_items[0]
            if item["type"] == "file":
                delete_action = menu.addAction("Delete File")
                delete_action.triggered.connect(lambda: self._delete_files([item]))

                # Add Select/Unselect file options
                select_file_action = menu.addAction("Select File")
                select_file_action.triggered.connect(lambda: self._select_files([item]))

                unselect_file_action = menu.addAction("Unselect File")
                unselect_file_action.triggered.connect(lambda: self._unselect_files([item]))
            elif item["type"] == "group":
                # Add Select/Unselect files options for group
                select_files_action = menu.addAction("Select Files")
                select_files_action.triggered.connect(lambda: self._select_files([item]))

                unselect_files_action = menu.addAction("Unselect Files")
                unselect_files_action.triggered.connect(lambda: self._unselect_files([item]))

            select_action = menu.addAction("Select by Field/Regex")
            select_action.triggered.connect(lambda: self._select_by_regex())

            remove_action = menu.addAction("Remove from List")
            remove_action.triggered.connect(lambda: self._remove_items_from_list([item]))
        else:
            # Multi-selection
            if files_only:
                # Only files selected
                delete_action = menu.addAction("Delete Files")
                delete_action.triggered.connect(lambda: self._delete_files(selected_items))

                select_files_action = menu.addAction("Select Files")
                select_files_action.triggered.connect(lambda: self._select_files(selected_items))

                unselect_files_action = menu.addAction("Unselect Files")
                unselect_files_action.triggered.connect(
                    lambda: self._unselect_files(selected_items)
                )

                remove_action = menu.addAction("Remove from List")
                remove_action.triggered.connect(
                    lambda: self._remove_items_from_list(selected_items)
                )
            else:
                # Has groups or mixed selection
                select_files_action = menu.addAction("Select Files")
                select_files_action.triggered.connect(lambda: self._select_files(selected_items))

                unselect_files_action = menu.addAction("Unselect Files")
                unselect_files_action.triggered.connect(
                    lambda: self._unselect_files(selected_items)
                )

                remove_action = menu.addAction("Remove from List")
                remove_action.triggered.connect(
                    lambda: self._remove_items_from_list(selected_items)
                )

        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _delete_file(self, index):
        # Implement the logic to delete the file at the given index
        try:
            if not self._deleter:
                QMessageBox.information(self, "Delete", "Delete service not available.")
                return

            file_path = self._get_file_path_from_index(index)
            if file_path:
                logger.info("Deleting file from context menu: {}", file_path)
                result = self._deleter.delete_to_recycle([file_path])
                if result.success_paths:
                    self._vm.remove_deleted_and_prune(result.success_paths)
                    self.refresh_tree(self._vm.groups)
                    self.statusBar().showMessage(
                        f"Deleted {len(result.success_paths)} file(s)", 3000
                    )
                if result.failed:
                    QMessageBox.warning(self, "Delete", f"Failed to delete: {result.failed[0][1]}")
        except Exception as e:
            logger.error("Delete file failed: {}", e)
            QMessageBox.critical(self, "Error", f"Delete failed: {str(e)}")

    def _select_by_regex(self):
        # Implement the logic to open the Select by Field/Regex dialog
        try:
            self.on_open_select_dialog()
        except Exception as e:
            logger.error("Select by regex failed: {}", e)

    def _remove_from_list(self, index):
        # Implement the logic to remove the file from the list
        try:
            file_path = self._get_file_path_from_index(index)
            if file_path:
                logger.info("Removing file from list: {}", file_path)
                self._vm.remove_from_list([file_path])
                self.refresh_tree(self._vm.groups)
                self.statusBar().showMessage("Removed file from list", 3000)
            else:
                logger.warning("No file path found for index")
        except Exception as e:
            logger.error("Remove from list failed: {}", e)

    def _remove_group_from_list(self, index):
        # Implement the logic to remove the entire group from the list
        try:
            group_number = self._get_group_number_from_index(index)
            if group_number is not None:
                # Find the group to see how many files it contains
                group_to_remove = None
                for g in self._vm.groups:
                    if g.group_number == group_number:
                        group_to_remove = g
                        break

                if group_to_remove:
                    file_count = len(group_to_remove.items)
                    logger.info(
                        "Removing group {} from list (contains {} files)", group_number, file_count
                    )

                    # Store groups count before removal for verification
                    groups_before = len(self._vm.groups)

                    self._vm.remove_group_from_list(group_number)

                    # Verify removal
                    groups_after = len(self._vm.groups)
                    logger.info("Groups before: {}, after: {}", groups_before, groups_after)

                    self.refresh_tree(self._vm.groups)
                    self.statusBar().showMessage(
                        f"Removed group {group_number} ({file_count} files) from list", 3000
                    )
                else:
                    logger.warning("Group {} not found in groups list", group_number)
                    QMessageBox.warning(self, "Remove from List", f"Group {group_number} not found")
            else:
                logger.warning("No group number found for index")
                QMessageBox.warning(self, "Remove from List", "Could not determine group number")
        except Exception as e:
            logger.error("Remove group from list failed: {}", e)
            QMessageBox.critical(self, "Error", f"Remove group from list failed: {str(e)}")

    # Deprecated: old dialogs no longer mounted on menu
    def on_edit_rules(self) -> None:  # pragma: no cover
        from app.views.dialogs.rules_dialog import RulesDialog

        dlg = RulesDialog(self)
        dlg.exec()

    def on_edit_filters(self) -> None:  # pragma: no cover
        from app.views.dialogs.filters_dialog import FiltersDialog

        dlg = FiltersDialog(self)
        dlg.exec()

    def on_open_select_dialog(self) -> None:
        try:
            from app.views.dialogs.select_dialog import SelectDialog
        except Exception:
            QMessageBox.critical(self, "Select", "Select dialog not available.")
            return
        fields = [
            "Group",
            "File Name",
            "Folder",
            "Size (Bytes)",
        ]
        row_values = self._get_highlighted_row_values()
        dlg = SelectDialog(fields=fields, parent=self, row_values=row_values)
        dlg.selectRequested.connect(
            lambda field, pattern: self._apply_select_regex(field, pattern, True)
        )
        dlg.unselectRequested.connect(
            lambda field, pattern: self._apply_select_regex(field, pattern, False)
        )
        dlg.exec()

    def _apply_select_regex(self, field: str, pattern: str, make_checked: bool) -> None:
        model = getattr(self, "_model", None)
        if model is None:
            return
        try:
            # Validate regex first for consistent UX
            re.compile(pattern)
        except Exception:
            QMessageBox.warning(self, "Regex", "Invalid regular expression.")
            return
        try:
            apply_select_regex(model, field, pattern, make_checked)
        except Exception:
            # Best effort; keep silent to avoid UX disruption
            pass

    def _get_highlighted_row_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            sel = self.tree.selectionModel()
            if not sel:
                return values
            rows = sel.selectedRows()
            if not rows:
                return values
            idx = rows[0]
            view_model = self.tree.model()
            src_model = getattr(self, "_model", None)
            proxy = getattr(self, "_proxy", None)
            if proxy is not None and hasattr(proxy, "mapToSource"):
                idx = proxy.mapToSource(idx)
                model = src_model
            else:
                model = view_model
            if idx.parent().isValid():
                # Child row (file)
                parent_idx = idx.parent()
                group_text = model.data(model.index(parent_idx.row(), 0, parent_idx.parent())) or ""
                name = model.data(model.index(idx.row(), 2, parent_idx)) or ""
                folder = model.data(model.index(idx.row(), 3, parent_idx)) or ""
                size_txt = model.data(model.index(idx.row(), 4, parent_idx)) or ""
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

    def show_group_counts(self, group_count: int) -> None:
        # No-op: groups sidebar removed; keep method to avoid breaking callers
        pass

    def show_groups_summary(self, groups: list) -> None:
        # No-op: groups sidebar removed; keep method to avoid breaking callers
        if not groups:
            return
        return

    def refresh_tree(self, groups: list) -> None:
        model, proxy = build_model(groups)
        if proxy is not None:
            proxy.setParent(self)
            self.tree.setModel(proxy)
            self._proxy = proxy
            self._model = model
            self.tree.sortByColumn(COL_GROUP, Qt.AscendingOrder)
        else:
            self.tree.setModel(model)
            self._proxy = None  # type: ignore[attr-defined]
            self._model = model
        # Expand all first so content-based width accounts for children
        try:
            self.tree.expandAll()
        except Exception:
            pass
        # Auto size columns to contents, then leave interactive for user drag
        try:
            header = self.tree.header()
            for i in range(NUM_COLUMNS):
                header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            self.tree.doItemsLayout()
            for i in range(NUM_COLUMNS):
                header.setSectionResizeMode(i, QHeaderView.Interactive)
        except Exception:
            for i in range(NUM_COLUMNS):
                self.tree.resizeColumnToContents(i)

        # Reconnect selection model after model reset
        self.tree.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

        # Adjust splitter so the tree fits visible content width
        try:
            tree_w = sum(self.tree.columnWidth(i) for i in range(NUM_COLUMNS)) + 24
            win_w = max(1, self.width())
            right_w = max(1, win_w - tree_w - 24)
            if right_w < 200:
                right_w = 200
            if tree_w < 200:
                tree_w = 200
            self._splitter.setSizes([tree_w, right_w])
        except Exception:
            pass

    # Selection -> preview
    def on_tree_selection_changed(self, *_: Any) -> None:
        indexes = self.tree.selectionModel().selectedRows()
        if not indexes:
            return
        idx = indexes[0]
        view_model = self.tree.model()
        src_model = getattr(self, "_model", None)
        proxy = getattr(self, "_proxy", None)
        if proxy is not None and hasattr(proxy, "mapToSource"):
            src_idx = proxy.mapToSource(idx)
            model = src_model
            idx = src_idx
        else:
            model = view_model
        # Determine if group or child
        _ = model.data(model.index(idx.row(), COL_GROUP, idx.parent()))
        if idx.parent().isValid():
            # Child row selected -> single preview
            name_index = model.index(idx.row(), COL_NAME, idx.parent())
            folder_index = model.index(idx.row(), COL_FOLDER, idx.parent())
            name = model.data(name_index)
            folder = model.data(folder_index)
            path = model.data(name_index, PATH_ROLE)
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
                    name_item = parent_item.child(r, COL_NAME)
                    folder_item = parent_item.child(r, COL_FOLDER)
                    name = model.itemFromIndex(name_item.index()).text() if name_item else ""
                    folder = model.itemFromIndex(folder_item.index()).text() if folder_item else ""
                    size_txt = (
                        model.itemFromIndex(parent_item.child(r, COL_SIZE_BYTES).index()).text()
                        if parent_item.child(r, COL_SIZE_BYTES)
                        else ""
                    )
                    if name and folder:
                        p = name_item.data(PATH_ROLE) if name_item else None
                        if not p:
                            p = str(Path(folder) / name)
                        group_items.append((p, name, folder, size_txt))
            self._preview.show_grid(group_items)

    def _gather_checked_paths(self) -> list[str]:
        model = getattr(self, "_model", None)
        if model is None:
            return []
        paths: list[str] = []
        root_count = model.rowCount()
        for r in range(root_count):
            parent_item = model.item(r, COL_GROUP)
            if parent_item is None:
                continue
            child_count = parent_item.rowCount()
            for cr in range(child_count):
                check_item = parent_item.child(cr, COL_SEL)
                name_item = parent_item.child(cr, COL_NAME)
                if check_item and check_item.checkState() == Qt.Checked and name_item:
                    p = name_item.data(PATH_ROLE)
                    if p:
                        paths.append(p)
        return paths

    def on_delete_selected(self) -> None:
        if not self._deleter:
            QMessageBox.information(self, "Delete", "Delete service not available.")
            return
        selected_paths = self._gather_checked_paths()
        if not selected_paths:
            QMessageBox.information(self, "Delete", "No items checked.")
            return
        from app.views.dialogs.delete_confirm_dialog import DeleteConfirmDialog

        plan = self._deleter.plan_delete(self._vm.groups, selected_paths)
        if self._settings and bool(self._settings.get("delete.confirm_group_full_delete", True)):
            dlg = DeleteConfirmDialog(plan.group_summaries, self)
            if dlg.exec() != QDialog.Accepted:
                return
        result = self._deleter.execute_delete(self._vm.groups, plan)
        # Notifications
        if result.success_paths:
            self.statusBar().showMessage(
                f"Deleted {len(result.success_paths)} items. Log: {getattr(result, 'log_path', '')}",
                5000,
            )
            try:
                # Best-effort info dialog for success (optional)
                QMessageBox.information(
                    self,
                    "Delete",
                    f"Deleted {len(result.success_paths)} items.\nLog: {getattr(result, 'log_path', '')}",
                )
            except Exception:
                pass
        if result.failed:
            QMessageBox.warning(self, "Delete", f"Failed: {len(result.failed)} items. See log.")

        # Update VM: remove deleted files and prune groups with only one file
        try:
            if result.success_paths:
                self._vm.remove_deleted_and_prune(result.success_paths)
                # Refresh tree view with updated groups
                self.refresh_tree(self._vm.groups)
        except Exception:
            pass

        # Prompt to update source CSV after list actions completed
        try:
            if result.success_paths:
                src = getattr(self._vm, "get_source_csv_path", lambda: None)()
                if src:
                    resp = QMessageBox.question(
                        self, "Update CSV?", f"Update source CSV file?\n{src}"
                    )
                    if resp == QMessageBox.Yes:
                        self._vm.export_csv(src)
                        self.statusBar().showMessage("CSV updated", 3000)
        except Exception as ex:
            logger.error("Update CSV after delete failed: {}", ex)

    # Actions
    def on_import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import CSV", "", "CSV Files (*.csv)")
        if not path:
            return
        try:
            self._vm.load_csv(path)
            self.show_group_counts(self._vm.group_count)
            self.show_groups_summary(self._vm.groups)
            self.refresh_tree(self._vm.groups)
            logger.info(
                "Imported CSV: {} | groups={} items={}",
                path,
                self._vm.group_count,
                sum(len(g.items) for g in self._vm.groups),
            )
            self.statusBar().showMessage(f"Imported {self._vm.group_count} groups", 3000)
        except Exception as ex:
            logger.exception("Import CSV failed: {}", ex)
            QMessageBox.critical(self, "Import Error", str(ex))
            self.statusBar().showMessage("Import failed", 3000)

    def on_export_csv(self) -> None:
        if not self._vm.groups:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "export.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            self._repo.save(path, self._vm.groups)
            logger.info(
                "Exported CSV: {} | groups={} items={} (bytes correct)",
                path,
                self._vm.group_count,
                sum(len(g.items) for g in self._vm.groups),
            )
            QMessageBox.information(self, "Export", "Export completed.")
            self.statusBar().showMessage("Export completed", 3000)
        except Exception as ex:
            logger.exception("Export CSV failed: {}", ex)
            QMessageBox.critical(self, "Export Error", str(ex))
            self.statusBar().showMessage("Export failed", 3000)

    # Slot for image results
    def _on_image_loaded(self, token: str, path: str, image: Any) -> None:
        self._preview.on_image_loaded(token, path, image)

    def _remove_from_list_toolbar(self):
        # Implement the logic to remove selected files or groups from the list via toolbar
        try:
            # First try to get checked files
            selected_paths = self._gather_checked_paths()
            if selected_paths:
                logger.info("Removing {} checked files from list via toolbar", len(selected_paths))
                self._vm.remove_from_list(selected_paths)
                self.refresh_tree(self._vm.groups)
                self.statusBar().showMessage(
                    f"Removed {len(selected_paths)} file(s) from list", 3000
                )
                return

            # If no checked files, try to get currently highlighted rows
            highlighted_items = self._gather_highlighted_items()
            if highlighted_items:
                logger.info(
                    "Removing {} highlighted items from list via toolbar", len(highlighted_items)
                )
                file_paths = [item for item in highlighted_items if item.get("type") == "file"]
                group_numbers = [item for item in highlighted_items if item.get("type") == "group"]

                if file_paths:
                    paths = [item["path"] for item in file_paths]
                    self._vm.remove_from_list(paths)

                if group_numbers:
                    for item in group_numbers:
                        self._vm.remove_group_from_list(item["group_number"])

                self.refresh_tree(self._vm.groups)
                self.statusBar().showMessage("Removed items from list", 3000)
                return

            QMessageBox.information(
                self,
                "Remove from List",
                "No items selected. Please check files or select rows first.",
            )

        except Exception as e:
            logger.error("Remove from list via toolbar failed: {}", e)
            QMessageBox.critical(self, "Error", f"Remove from list failed: {str(e)}")

    def _get_file_path_from_index(self, index):
        try:
            view_model = self.tree.model()
            src_model = getattr(self, "_model", None)
            proxy = getattr(self, "_proxy", None)

            # Handle proxy model
            if proxy is not None and hasattr(proxy, "mapToSource"):
                src_index = proxy.mapToSource(index)
                model = src_model
                idx = src_index
            else:
                model = view_model
                idx = index

            if idx.isValid() and idx.parent().isValid():
                # This is a file row - get the path from the name column
                name_index = model.index(idx.row(), COL_NAME, idx.parent())
                return model.data(name_index, PATH_ROLE)
        except Exception as e:
            logger.error("Error getting file path from index: {}", e)
        return None

    def _get_group_number_from_index(self, index):
        try:
            view_model = self.tree.model()
            src_model = getattr(self, "_model", None)
            proxy = getattr(self, "_proxy", None)

            # Handle proxy model
            if proxy is not None and hasattr(proxy, "mapToSource"):
                src_index = proxy.mapToSource(index)
                model = src_model
                idx = src_index
            else:
                model = view_model
                idx = index

            if idx.isValid() and not idx.parent().isValid():
                # This is a group row - try to get group number from SORT_ROLE first

                # Get the group item (first column)
                group_index = model.index(idx.row(), COL_GROUP, idx.parent())

                # Try SORT_ROLE first (most reliable)
                group_num = model.data(group_index, SORT_ROLE)
                if group_num is not None:
                    logger.debug("Got group number from SORT_ROLE: {}", group_num)
                    return int(group_num)

                # Fallback to parsing display text
                group_text = model.data(group_index, Qt.DisplayRole)
                logger.debug("Group text from index: '{}'", group_text)

                if group_text and isinstance(group_text, str) and group_text.startswith("Group "):
                    try:
                        group_num = int(group_text.split(" ")[1])
                        logger.debug("Extracted group number from text: {}", group_num)
                        return group_num
                    except (IndexError, ValueError) as e:
                        logger.error("Failed to parse group number from '{}': {}", group_text, e)
                else:
                    logger.warning("Invalid group text format: '{}'", group_text)
        except Exception as e:
            logger.error("Error getting group number from index: {}", e)
        return None

    def _gather_highlighted_items(self):
        """Gather currently highlighted (selected) items in the tree view."""
        highlighted_items = []
        try:
            selection_model = self.tree.selectionModel()
            if not selection_model:
                return highlighted_items

            selected_indexes = selection_model.selectedRows()
            for index in selected_indexes:
                if index.parent().isValid():
                    # File row
                    file_path = self._get_file_path_from_index(index)
                    if file_path:
                        highlighted_items.append({"type": "file", "path": file_path})
                else:
                    # Group row
                    group_number = self._get_group_number_from_index(index)
                    if group_number is not None:
                        highlighted_items.append({"type": "group", "group_number": group_number})
        except Exception as e:
            logger.error("Error gathering highlighted items: {}", e)
        return highlighted_items

    def _get_selected_items(self):
        """Get all currently selected items (same as _gather_highlighted_items but with better name for context menu)."""
        return self._gather_highlighted_items()

    def _delete_files(self, items):
        """Delete multiple files from the given items list."""
        try:
            if not self._deleter:
                QMessageBox.information(self, "Delete", "Delete service not available.")
                return

            # Extract file paths from items
            file_paths = []
            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])

            if not file_paths:
                QMessageBox.information(self, "Delete", "No files to delete.")
                return

            logger.info("Deleting {} files from context menu", len(file_paths))
            result = self._deleter.delete_to_recycle(file_paths)

            if result.success_paths:
                self._vm.remove_deleted_and_prune(result.success_paths)
                self.refresh_tree(self._vm.groups)
                self.statusBar().showMessage(f"Deleted {len(result.success_paths)} file(s)", 3000)

            if result.failed:
                failed_msg = f"Failed to delete {len(result.failed)} file(s)"
                QMessageBox.warning(self, "Delete", failed_msg)

        except Exception as e:
            logger.error("Delete files failed: {}", e)
            QMessageBox.critical(self, "Error", f"Delete failed: {str(e)}")

    def _select_files(self, items):
        """Mark selected files as checked (set sel checkbox)."""
        try:
            model = getattr(self, "_model", None)
            if model is None:
                return

            # Get all file paths from items (including files within selected groups)
            file_paths = []
            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])
                elif item["type"] == "group":
                    # Get all files in the group
                    for g in self._vm.groups:
                        if g.group_number == item["group_number"]:
                            file_paths.extend([f.file_path for f in g.items])

            if not file_paths:
                return

            # Mark files as checked in the model
            checked_count = 0
            root_count = model.rowCount()
            for r in range(root_count):
                parent_item = model.item(r, COL_GROUP)
                if parent_item is None:
                    continue
                child_count = parent_item.rowCount()
                for cr in range(child_count):
                    name_item = parent_item.child(cr, COL_NAME)
                    check_item = parent_item.child(cr, COL_SEL)
                    if name_item and check_item:
                        file_path = name_item.data(PATH_ROLE)
                        if file_path in file_paths:
                            check_item.setCheckState(Qt.Checked)
                            checked_count += 1

            logger.info("Marked {} files as selected", checked_count)
            self.statusBar().showMessage(f"Marked {checked_count} file(s) as selected", 3000)

        except Exception as e:
            logger.error("Select files failed: {}", e)

    def _unselect_files(self, items):
        """Mark selected files as unchecked (unset sel checkbox)."""
        try:
            model = getattr(self, "_model", None)
            if model is None:
                return

            # Get all file paths from items (including files within selected groups)
            file_paths = []
            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])
                elif item["type"] == "group":
                    # Get all files in the group
                    for g in self._vm.groups:
                        if g.group_number == item["group_number"]:
                            file_paths.extend([f.file_path for f in g.items])

            if not file_paths:
                return

            # Mark files as unchecked in the model
            unchecked_count = 0
            root_count = model.rowCount()
            for r in range(root_count):
                parent_item = model.item(r, COL_GROUP)
                if parent_item is None:
                    continue
                child_count = parent_item.rowCount()
                for cr in range(child_count):
                    name_item = parent_item.child(cr, COL_NAME)
                    check_item = parent_item.child(cr, COL_SEL)
                    if name_item and check_item:
                        file_path = name_item.data(PATH_ROLE)
                        if file_path in file_paths:
                            check_item.setCheckState(Qt.Unchecked)
                            unchecked_count += 1

            logger.info("Unmarked {} files as unselected", unchecked_count)
            self.statusBar().showMessage(f"Unmarked {unchecked_count} file(s) as unselected", 3000)

        except Exception as e:
            logger.error("Unselect files failed: {}", e)

    def _remove_items_from_list(self, items):
        """Remove multiple items (files and/or groups) from the list."""
        try:
            file_paths = []
            group_numbers = []

            # Separate files and groups
            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])
                elif item["type"] == "group":
                    group_numbers.append(item["group_number"])

            # Remove files first
            if file_paths:
                logger.info("Removing {} files from list", len(file_paths))
                self._vm.remove_from_list(file_paths)

            # Remove groups
            if group_numbers:
                logger.info("Removing {} groups from list", len(group_numbers))
                for group_num in group_numbers:
                    self._vm.remove_group_from_list(group_num)

            self.refresh_tree(self._vm.groups)

            total_removed = len(file_paths) + len(group_numbers)
            self.statusBar().showMessage(f"Removed {total_removed} item(s) from list", 3000)

        except Exception as e:
            logger.error("Remove items from list failed: {}", e)
            QMessageBox.critical(self, "Error", f"Remove from list failed: {str(e)}")
