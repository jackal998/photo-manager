from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Photo Manager - M1")
        central = QWidget(self)
        root = QHBoxLayout(central)

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
        # groups: list[PhotoGroup]
        if not groups:
            return
        # First line remains the total
        self.groups_list.addItem(QListWidgetItem(""))
        for g in groups:
            count = len(getattr(g, "items", []) or [])
            self.groups_list.addItem(QListWidgetItem(f"Group {g.group_number} ({count})"))
