from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QHBoxLayout, QPushButton


class FiltersDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Filters")

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Filters (placeholder)"))

        self.regex_edit = QLineEdit()
        self.regex_edit.setPlaceholderText("Path regex, e.g. .*\\iPhone\\2023\\02\\.*")
        root.addWidget(self.regex_edit)

        btns = QHBoxLayout()
        self.btn_apply = QPushButton("Apply")
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_apply)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        root.addLayout(btns)

        self.btn_close.clicked.connect(self.accept)


