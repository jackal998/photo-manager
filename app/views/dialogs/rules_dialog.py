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
        self.btn_execute = QPushButton("Execute")
        self.btn_undo = QPushButton("Undo")
        self.btn_redo = QPushButton("Redo")
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_load)
        btns.addWidget(self.btn_execute)
        btns.addWidget(self.btn_undo)
        btns.addWidget(self.btn_redo)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        root.addLayout(btns)

        self.btn_close.clicked.connect(self.accept)


