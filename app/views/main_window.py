from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Dict, List

from loguru import logger
from PySide6.QtWidgets import (
    QMainWindow,
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
)
from PySide6.QtGui import QStandardItemModel, QStandardItem, QPixmap
from PySide6.QtCore import Qt, QThreadPool, QRunnable, QObject, Signal, QSize


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

    def __init__(self, vm: Any, repo: Any, image_service: Any | None = None, settings: Any | None = None) -> None:
        super().__init__()
        self._vm = vm
        self._repo = repo
        self._img = image_service
        self._settings = settings
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
        file_menu.addSeparator()
        self.action_exit = file_menu.addAction("Exit")
        tools_menu = menubar.addMenu("Tools")
        self.action_edit_rules = tools_menu.addAction("Edit Rules…")
        self.action_edit_filters = tools_menu.addAction("Edit Filters…")
        self.setMenuBar(menubar)

        self.action_import.triggered.connect(self.on_import_csv)
        self.action_export.triggered.connect(self.on_export_csv)
        self.action_exit.triggered.connect(self.close)
        self.action_edit_rules.triggered.connect(self.on_edit_rules)
        self.action_edit_filters.triggered.connect(self.on_edit_filters)

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

    def on_edit_rules(self) -> None:
        from app.views.dialogs.rules_dialog import RulesDialog

        dlg = RulesDialog(self)
        dlg.exec()

    def on_edit_filters(self) -> None:
        from app.views.dialogs.filters_dialog import FiltersDialog

        dlg = FiltersDialog(self)
        dlg.exec()

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
        model.setHorizontalHeaderLabels(["Group", "File Name", "Folder", "Size (Bytes)"])
        for g in groups:
            group_item = QStandardItem(f"Group {g.group_number}")
            group_item.setEditable(False)
            group_row = [group_item, QStandardItem(""), QStandardItem(""), QStandardItem(str(len(getattr(g, 'items', []) or [])))]
            for it in group_row:
                it.setEditable(False)
            model.appendRow(group_row)
            for p in getattr(g, "items", []) or []:
                name = Path(p.file_path).name
                folder = p.folder_path
                size = str(p.file_size_bytes)
                child_row = [
                    QStandardItem(""),
                    QStandardItem(name),
                    QStandardItem(folder),
                    QStandardItem(size),
                ]
                for it in child_row:
                    it.setEditable(False)
                # Store authoritative full path on the name item to avoid mismatches
                try:
                    child_row[1].setData(p.file_path, Qt.UserRole)
                except Exception:
                    pass
                group_item.appendRow(child_row)
        self.tree.setModel(model)
        for i in range(4):
            self.tree.resizeColumnToContents(i)

        # Reconnect selection model after model reset
        self.tree.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)

    # Selection -> preview
    def on_tree_selection_changed(self, *_: Any) -> None:
        indexes = self.tree.selectionModel().selectedRows()
        if not indexes:
            return
        idx = indexes[0]
        model = self.tree.model()
        # Determine if group or child
        group_text = model.data(model.index(idx.row(), 0, idx.parent()))
        if idx.parent().isValid():
            # Child row selected -> single preview
            name_index = model.index(idx.row(), 1, idx.parent())
            folder_index = model.index(idx.row(), 2, idx.parent())
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
                    name_item = parent_item.child(r, 1)
                    folder_item = parent_item.child(r, 2)
                    name = model.itemFromIndex(name_item.index()).text() if name_item else ""
                    folder = model.itemFromIndex(folder_item.index()).text() if folder_item else ""
                    size_txt = model.itemFromIndex(parent_item.child(r, 3).index()).text() if parent_item.child(r, 3) else ""
                    if name and folder:
                        p = name_item.data(Qt.UserRole) if name_item else None
                        if not p:
                            p = str(Path(folder) / name)
                        group_items.append((p, name, folder, size_txt))
            self._show_group_grid(group_items)

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
        # Single: 1x original; allow scrollbars for large images
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
        # Grid: no horizontal scroll; dynamic columns/cell size 200-600
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
        min_px, max_px = 200, 600
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
            self._apply_single_pixmap_fit()

    def _apply_single_pixmap_fit(self) -> None:
        if not self._single_pm:
            return
        vp = self.preview_area.viewport()
        max_w = max(1, vp.width())
        pm = self._single_pm
        if pm.width() > max_w:
            scaled = pm.scaledToWidth(max_w, Qt.SmoothTransformation)
            self._single_label.setPixmap(scaled)
            self._single_label.adjustSize()
        else:
            self._single_label.setPixmap(pm)
            self._single_label.adjustSize()
