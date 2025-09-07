from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenuBar,
    QFileDialog,
    QMessageBox,
)


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
        self.setMenuBar(menubar)

        self.action_import.triggered.connect(self.on_import_csv)
        self.action_export.triggered.connect(self.on_export_csv)
        self.action_exit.triggered.connect(self.close)

        # Left panel: rules placeholder
        left = QVBoxLayout()
        left.addWidget(QLabel("Rules / Filters"))
        self.rules_placeholder = QListWidget()
        left.addWidget(self.rules_placeholder)

        # Center: groups list placeholder
        center = QVBoxLayout()
        center.addWidget(QLabel("Groups"))
        self.groups_list = QListWidget()
        center.addWidget(self.groups_list)

        # Right: preview placeholder
        right = QVBoxLayout()
        right.addWidget(QLabel("Preview"))
        self.preview_label = QLabel("(preview placeholder)")
        right.addWidget(self.preview_label)

        root.addLayout(left, 2)
        root.addLayout(center, 5)
        root.addLayout(right, 3)

        self.setCentralWidget(central)

    def show_group_counts(self, group_count: int) -> None:
        self.groups_list.clear()
        self.groups_list.addItem(QListWidgetItem(f"Groups: {group_count}"))

    def show_groups_summary(self, groups: list) -> None:
        if not groups:
            return
        self.groups_list.addItem(QListWidgetItem(""))
        for g in groups:
            count = len(getattr(g, "items", []) or [])
            self.groups_list.addItem(QListWidgetItem(f"Group {g.group_number} ({count})"))

    # Actions
    def on_import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import CSV", "", "CSV Files (*.csv)")
        if not path:
            return
        try:
            self._vm.load_csv(path)
            self.show_group_counts(self._vm.group_count)
            self.show_groups_summary(self._vm.groups)
        except Exception as ex:
            QMessageBox.critical(self, "Import Error", str(ex))

    def on_export_csv(self) -> None:
        if not self._vm.groups:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "export.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            self._repo.save(path, self._vm.groups)
            QMessageBox.information(self, "Export", "Export completed.")
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", str(ex))
