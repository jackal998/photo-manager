from __future__ import annotations

from pathlib import Path
from typing import Any

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
)
from PySide6.QtGui import QStandardItemModel, QStandardItem


class MainWindow(QMainWindow):
    def __init__(self, vm: Any, repo: Any) -> None:
        super().__init__()
        self._vm = vm
        self._repo = repo

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

        # Center: single tree view (groups/items)
        center = QVBoxLayout()
        self.tree = QTreeView()
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        center.addWidget(self.tree)

        # Right: preview placeholder
        right = QVBoxLayout()
        right.addWidget(QLabel("Preview"))
        self.preview_label = QLabel("(preview placeholder)")
        right.addWidget(self.preview_label)

        # Root layout: center + right only
        root.addLayout(center, 7)
        root.addLayout(right, 3)

        self.setCentralWidget(central)

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
                group_item.appendRow(child_row)
        self.tree.setModel(model)
        for i in range(4):
            self.tree.resizeColumnToContents(i)

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
