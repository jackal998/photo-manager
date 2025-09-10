from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Dict, List

from loguru import logger
from PySide6.QtWidgets import (
    QMainWindow,
    QApplication,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QMenuBar,
    QFileDialog,
    QMessageBox,
    QTreeView,
    QDialog,
    QScrollArea,
    QGridLayout,
    QSplitter,
    QHeaderView,
)
from PySide6.QtGui import QStandardItemModel, QStandardItem, QPixmap
from PySide6.QtCore import Qt, QThreadPool, QRunnable, QObject, Signal, QSortFilterProxyModel, QEvent
import re


class _ImageTask(QRunnable):
    def __init__(self, *, path: str, side: int, is_preview: bool, service: Any, receiver: QObject, token: str) -> None:
        super().__init__()
        self._path = path
        self._side = side
        self._is_preview = is_preview
        self._service = service
        self._receiver = receiver
        self._token = token

    def run(self) -> None:  # type: ignore[override]
        try:
            if self._is_preview:
                img = self._service.get_preview(self._path, self._side)
            else:
                img = self._service.get_thumbnail(self._path, self._side)
        except Exception as ex:
            logger.error("Image task failed: {}", ex)
            img = None
        # Emit on receiver (MainWindow has a slot)
        try:
            self._receiver.imageLoaded.emit(self._token, self._path, img)
        except Exception:
            pass


class MainWindow(QMainWindow):
    imageLoaded = Signal(str, str, object)  # token, path, QImage

    def __init__(self, vm: Any, repo: Any, image_service: Any | None = None, settings: Any | None = None, delete_service: Any | None = None) -> None:
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
        self.setMenuBar(menubar)

        self.action_import.triggered.connect(self.on_import_csv)
        self.action_export.triggered.connect(self.on_export_csv)
        self.action_exit.triggered.connect(self.close)
        self.action_delete.triggered.connect(self.on_delete_selected)
        self.action_select_by.triggered.connect(self.on_open_select_dialog)

        # Status bar
        self.statusBar().showMessage("Ready", 3000)

        # Center: tree view (groups/items)
        center_widget = QWidget()
        center = QVBoxLayout(center_widget)
        self.tree = QTreeView()
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        center.addWidget(self.tree)

        # Right: preview area (single image or grid)
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        right.addWidget(QLabel("Preview"))
        self.preview_area = QScrollArea()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.preview_area.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._preview_container = QWidget()
        self._preview_layout = QVBoxLayout(self._preview_container)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)
        self._single_label = QLabel("(preview)")
        self._single_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._single_label.setMinimumHeight(200)
        self._preview_layout.addWidget(self._single_label)
        self.preview_area.setWidget(self._preview_container)
        right.addWidget(self.preview_area)

        # Splitter to allow resizable boundary
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        self._splitter = splitter
        # Refit on splitter moves (handles tree/preview border drags)
        try:
            self._splitter.splitterMoved.connect(lambda *_: self._apply_single_pixmap_fit())
        except Exception:
            pass

        # Root layout: splitter only
        root.addWidget(splitter)

        self.setCentralWidget(central)

        # Thread pool for background image loading
        self._pool = QThreadPool.globalInstance()
        self.imageLoaded.connect(self._on_image_loaded)
        self._current_single_token: Optional[str] = None
        self._grid_labels: Dict[str, QLabel] = {}
        self._grid_container: Optional[QWidget] = None
        self._grid_layout: Optional[QGridLayout] = None
        self._grid_paths: List[str] = []
        self._grid_items: List[tuple[str, str, str, str]] = []  # (path, name, folder, size)
        self._single_pm: Optional[QPixmap] = None

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

        # Track preview viewport resizes to keep fit-on-width accurate
        try:
            self.preview_area.viewport().installEventFilter(self)
            self.preview_area.installEventFilter(self)
        except Exception:
            pass

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
        dlg.selectRequested.connect(lambda field, pattern: self._apply_select_regex(field, pattern, True))
        dlg.unselectRequested.connect(lambda field, pattern: self._apply_select_regex(field, pattern, False))
        dlg.exec()

    def _apply_select_regex(self, field: str, pattern: str, make_checked: bool) -> None:
        model = getattr(self, "_model", None)
        if model is None:
            return
        try:
            rx = re.compile(pattern)
        except Exception:
            QMessageBox.warning(self, "Regex", "Invalid regular expression.")
            return
        root_count = model.rowCount()
        for r in range(root_count):
            parent_item = model.item(r, 0)
            if parent_item is None:
                continue
            if field == "Group":
                group_text = parent_item.text() or ""
                if rx.search(group_text or ""):
                    # Apply to all children
                    for cr in range(parent_item.rowCount()):
                        check_item = parent_item.child(cr, 1)
                        if check_item is not None and check_item.isCheckable():
                            check_item.setCheckState(Qt.Checked if make_checked else Qt.Unchecked)
                continue
            # Else match per child
            for cr in range(parent_item.rowCount()):
                target_text = ""
                if field == "File Name":
                    item = parent_item.child(cr, 2)
                    target_text = item.text() if item else ""
                elif field == "Folder":
                    item = parent_item.child(cr, 3)
                    target_text = item.text() if item else ""
                elif field == "Size (Bytes)":
                    item = parent_item.child(cr, 4)
                    target_text = item.text() if item else ""
                if target_text and rx.search(target_text):
                    check_item = parent_item.child(cr, 1)
                    if check_item is not None and check_item.isCheckable():
                        check_item.setCheckState(Qt.Checked if make_checked else Qt.Unchecked)

    def _get_highlighted_row_values(self) -> Dict[str, str]:
        values: Dict[str, str] = {}
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
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["Group", "Sel", "File Name", "Folder", "Size (Bytes)", "Group Count"])
        for g in groups:
            group_item = QStandardItem(f"Group {g.group_number}")
            group_item.setEditable(False)
            try:
                # Numeric sort for group column
                group_item.setData(int(getattr(g, 'group_number', 0) or 0), Qt.UserRole + 1)
            except Exception:
                pass
            # Group row has NO selection checkbox (checkboxes only on files)
            group_count_val = len(getattr(g, 'items', []) or [])
            group_row = [group_item, QStandardItem(""), QStandardItem(""), QStandardItem(""), QStandardItem(""), QStandardItem(str(group_count_val))]
            try:
                # Numeric sort for group count
                group_row[5].setData(int(group_count_val), Qt.UserRole + 1)
            except Exception:
                pass
            for it in group_row:
                it.setEditable(False)
            model.appendRow(group_row)
            for p in getattr(g, "items", []) or []:
                name = Path(p.file_path).name
                folder = p.folder_path
                size_num = int(getattr(p, 'file_size_bytes', 0) or 0)
                check = QStandardItem("")
                check.setCheckable(True)
                check.setEditable(False)
                child_row = [
                    QStandardItem(""),
                    check,
                    QStandardItem(name),
                    QStandardItem(folder),
                    QStandardItem(str(size_num)),
                    QStandardItem(""),
                ]
                # Sort roles for child row
                try:
                    child_row[1].setData(0, Qt.UserRole + 1)  # Sel default unchecked
                except Exception:
                    pass
                try:
                    child_row[2].setData(str(name).lower(), Qt.UserRole + 1)  # File Name text sort (ci)
                except Exception:
                    pass
                try:
                    child_row[3].setData(str(folder).lower(), Qt.UserRole + 1)  # Folder text sort (ci)
                except Exception:
                    pass
                try:
                    child_row[4].setData(int(size_num), Qt.UserRole + 1)  # Size numeric sort
                except Exception:
                    pass
                for it in child_row:
                    it.setEditable(False)
                # Store authoritative full path on the name item to avoid mismatches
                try:
                    child_row[2].setData(p.file_path, Qt.UserRole)
                except Exception:
                    pass
                group_item.appendRow(child_row)
        # Install proxy for numeric/text sort with roles
        try:
            proxy = QSortFilterProxyModel(self)
            proxy.setSortRole(Qt.UserRole + 1)
            proxy.setSortCaseSensitivity(Qt.CaseInsensitive)
            proxy.setSourceModel(model)
            self.tree.setModel(proxy)
            self._proxy = proxy
            self._model = model
            # Default sort by Group numerically
            self.tree.sortByColumn(0, Qt.AscendingOrder)
        except Exception:
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
            for i in range(6):
                header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            self.tree.doItemsLayout()
            for i in range(6):
                header.setSectionResizeMode(i, QHeaderView.Interactive)
        except Exception:
            for i in range(6):
                self.tree.resizeColumnToContents(i)

        # Reconnect selection model after model reset
        self.tree.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

        # Adjust splitter so the tree fits visible content width
        try:
            total_cols = 6
            tree_w = sum(self.tree.columnWidth(i) for i in range(total_cols)) + 24
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
        group_text = model.data(model.index(idx.row(), 0, idx.parent()))
        if idx.parent().isValid():
            # Child row selected -> single preview
            name_index = model.index(idx.row(), 2, idx.parent())
            folder_index = model.index(idx.row(), 3, idx.parent())
            name = model.data(name_index)
            folder = model.data(folder_index)
            path = model.data(name_index, Qt.UserRole)
            if not path:
                if not folder or not name:
                    return
                path = str(Path(folder) / name)
            self._show_single_preview(path)
        else:
            # Group level selected -> grid thumbnails
            group_items: List[tuple[str, str, str, str]] = []
            parent_item = model.itemFromIndex(model.index(idx.row(), 0))
            if parent_item is not None:
                rows = parent_item.rowCount()
                for r in range(rows):
                    name_item = parent_item.child(r, 2)
                    folder_item = parent_item.child(r, 3)
                    name = model.itemFromIndex(name_item.index()).text() if name_item else ""
                    folder = model.itemFromIndex(folder_item.index()).text() if folder_item else ""
                    size_txt = model.itemFromIndex(parent_item.child(r, 4).index()).text() if parent_item.child(r, 4) else ""
                    if name and folder:
                        p = name_item.data(Qt.UserRole) if name_item else None
                        if not p:
                            p = str(Path(folder) / name)
                        group_items.append((p, name, folder, size_txt))
            self._show_group_grid(group_items)

    def _gather_checked_paths(self) -> list[str]:
        model = getattr(self, "_model", None)
        if model is None:
            return []
        paths: list[str] = []
        root_count = model.rowCount()
        for r in range(root_count):
            parent_item = model.item(r, 0)
            if parent_item is None:
                continue
            child_count = parent_item.rowCount()
            for cr in range(child_count):
                check_item = parent_item.child(cr, 1)
                name_item = parent_item.child(cr, 2)
                if check_item and check_item.checkState() == Qt.Checked and name_item:
                    p = name_item.data(Qt.UserRole)
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
            self.statusBar().showMessage(f"Deleted {len(result.success_paths)} items. Log: {getattr(result, 'log_path', '')}", 5000)
            try:
                # Best-effort info dialog for success (optional)
                QMessageBox.information(self, "Delete", f"Deleted {len(result.success_paths)} items.\nLog: {getattr(result, 'log_path', '')}")
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
                    resp = QMessageBox.question(self, "Update CSV?", f"Update source CSV file?\n{src}")
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
            logger.info("Imported CSV: {} | groups={} items={}", path, self._vm.group_count, sum(len(g.items) for g in self._vm.groups))
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
            logger.info("Exported CSV: {} | groups={} items={} (bytes correct)", path, self._vm.group_count, sum(len(g.items) for g in self._vm.groups))
            QMessageBox.information(self, "Export", "Export completed.")
            self.statusBar().showMessage("Export completed", 3000)
        except Exception as ex:
            logger.exception("Export CSV failed: {}", ex)
            QMessageBox.critical(self, "Export Error", str(ex))
            self.statusBar().showMessage("Export failed", 3000)

    # Preview helpers
    def _clear_preview(self) -> None:
        if self._grid_container is not None:
            self._preview_layout.removeWidget(self._grid_container)
            self._grid_container.deleteLater()
            self._grid_container = None
            self._grid_layout = None
        self._grid_labels.clear()
        self._grid_paths = []
        self._single_label.clear()
        self._single_label.setVisible(False)
        self._single_pm = None

    def _show_single_preview(self, path: str) -> None:
        self._clear_preview()
        # Single: original size; enable scrollbars when content exceeds viewport
        self.preview_area.setWidgetResizable(False)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._single_label.setVisible(True)
        self._single_label.setText("Loading…")
        side = 0
        token = f"single|{path}|{side}"
        self._current_single_token = token
        if self._img is None:
            self._single_label.setText("(no image service)")
            return
        task = _ImageTask(path=path, side=side, is_preview=True, service=self._img, receiver=self, token=token)
        self._pool.start(task)

    def _show_group_grid(self, items: List[tuple[str, str, str, str]]) -> None:
        self._clear_preview()
        # Grid: no horizontal scroll; allow vertical scroll; dynamic geometry
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._grid_paths = [p for p, _, _, _ in items]
        self._grid_items = list(items)
        # Create grid container
        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(4)
        self._apply_grid_margins()
        cols, thumb_side = self._compute_grid_geometry()
        self._grid_labels = {}
        for i, it in enumerate(items):
            p, name, folder, size_txt = it
            r, c = divmod(i, cols)
            tile = QWidget()
            v = QVBoxLayout(tile)
            v.setContentsMargins(0, 0, 0, 0)
            img_lbl = QLabel("Loading…")
            img_lbl.setFixedSize(thumb_side, thumb_side)
            img_lbl.setAlignment(Qt.AlignCenter)
            v.addWidget(img_lbl)
            # Text info under image
            # Use concise two-line label + size
            info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes")
            info.setWordWrap(True)
            v.addWidget(info)
            self._grid_layout.addWidget(tile, r, c)
            self._grid_labels[f"grid|{p}|{thumb_side}"] = img_lbl
            if self._img is not None:
                task = _ImageTask(path=p, side=thumb_side, is_preview=False, service=self._img, receiver=self, token=f"grid|{p}|{thumb_side}")
                self._pool.start(task)
        self._preview_layout.addWidget(self._grid_container)

    def _apply_grid_margins(self) -> None:
        if self._grid_layout is None:
            return
        vp = self.preview_area.viewport()
        w = max(1, vp.width())
        h = max(1, vp.height())
        m_lr = int(w * 0.05)
        m_tb = int(h * 0.05)
        self._grid_layout.setContentsMargins(m_lr, m_tb, m_lr, m_tb)

    def _compute_grid_geometry(self) -> tuple[int, int]:
        viewport = self.preview_area.viewport()
        width = max(1, viewport.width())
        spacing = 4
        min_px = 200
        try:
            max_px = int(self._thumb_size) if int(self._thumb_size) > 0 else 600
        except Exception:
            max_px = 600
        best_cols = 1
        best_cell = min_px
        for cols in range(1, 64):
            total_spacing = spacing * (cols - 1)
            cell = (width - total_spacing) // cols
            if cell < min_px:
                break
            cand = min(cell, max_px)
            if cand >= best_cell:
                best_cell = cand
                best_cols = cols
        return best_cols, best_cell

    # Slot for image results
    def _on_image_loaded(self, token: str, path: str, image: Any) -> None:
        try:
            if token.startswith("single|"):
                if token != self._current_single_token:
                    return
                if image is None:
                    self._single_label.setText("(failed)")
                    return
                pm = QPixmap.fromImage(image)
                if pm.isNull():
                    self._single_label.setText("(failed)")
                    return
                self._single_pm = pm
                # Fit to current viewport width (avoid horizontal scroll), keep aspect ratio
                self._apply_single_pixmap_fit()
                self._single_label.setText("")
            elif token.startswith("grid|"):
                lbl = self._grid_labels.get(token)
                if not lbl or image is None:
                    return
                pm = QPixmap.fromImage(image)
                if pm.isNull():
                    lbl.setText("(failed)")
                    return
                lbl.setPixmap(pm.scaled(lbl.width(), lbl.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                lbl.setText("")
        except Exception as ex:
            logger.error("Update preview failed: {}", ex)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # Recompute grid on resize
        if self._grid_container is not None and self._grid_layout is not None and self._grid_items:
            self._preview_layout.removeWidget(self._grid_container)
            self._grid_container.deleteLater()
            self._grid_container = QWidget()
            self._grid_layout = QGridLayout(self._grid_container)
            self._grid_layout.setSpacing(4)
            self._apply_grid_margins()
            cols, thumb_side = self._compute_grid_geometry()
            self._grid_labels = {}
            for i, it in enumerate(self._grid_items):
                p, name, folder, size_txt = it
                r, c = divmod(i, cols)
                tile = QWidget()
                v = QVBoxLayout(tile)
                v.setContentsMargins(0, 0, 0, 0)
                img_lbl = QLabel("Loading…")
                img_lbl.setFixedSize(thumb_side, thumb_side)
                img_lbl.setAlignment(Qt.AlignCenter)
                v.addWidget(img_lbl)
                info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes")
                info.setWordWrap(True)
                v.addWidget(info)
                self._grid_layout.addWidget(tile, r, c)
                self._grid_labels[f"grid|{p}|{thumb_side}"] = img_lbl
                if self._img is not None:
                    task = _ImageTask(path=p, side=thumb_side, is_preview=False, service=self._img, receiver=self, token=f"grid|{p}|{thumb_side}")
                    self._pool.start(task)
            self._preview_layout.addWidget(self._grid_container)
        else:
            # Re-apply width fit for single-image preview on window resize
            self._apply_single_pixmap_fit()

    def _apply_single_pixmap_fit(self) -> None:
        # Scale to viewport width (avoid horizontal scroll), keep aspect ratio. Do not scale up.
        try:
            # Skip when in grid mode
            if self._grid_container is not None and self._grid_items:
                return
            if self._single_pm is None or self._single_pm.isNull():
                return
            vp = self.preview_area.viewport()
            max_w = max(1, vp.width())
            pm = self._single_pm
            target_w = min(pm.width(), max_w - 1)  # subtract 1px to avoid bar jitter
            if target_w <= 0:
                target_w = pm.width()
            if pm.width() != target_w:
                scaled = pm.scaledToWidth(target_w, Qt.SmoothTransformation)
                self._single_label.setPixmap(scaled)
            else:
                self._single_label.setPixmap(pm)
            self._single_label.adjustSize()
            try:
                # Ensure container updates its size for accurate scrollbars
                self._preview_container.adjustSize()
            except Exception:
                pass
        except Exception:
            pass

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        try:
            if event and event.type() == QEvent.Resize:
                if obj is self.preview_area or obj is self.preview_area.viewport():
                    self._apply_single_pixmap_fit()
        except Exception:
            pass
        return super().eventFilter(obj, event)
