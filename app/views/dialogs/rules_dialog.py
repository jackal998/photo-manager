from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QListWidget, QHBoxLayout, QPushButton


class RulesDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Rules")

        root = QVBoxLayout(self)
        root.addWidget(QLabel("Rules (placeholder)"))

        self.list = QListWidget()
        self.list.addItem("Sample rule (not implemented)")
        root.addWidget(self.list)

        btns = QHBoxLayout()
        self.btn_load = QPushButton("Load…")
        self.btn_save = QPushButton("Save…")
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_load)
        btns.addWidget(self.btn_save)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        root.addLayout(btns)

        self.btn_close.clicked.connect(self.accept)


