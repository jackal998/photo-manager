from __future__ import annotations

from typing import List, Dict, Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QPushButton,
)


class SelectDialog(QDialog):
    selectRequested = Signal(str, str)  # field, regex
    unselectRequested = Signal(str, str)  # field, regex

    def __init__(self, fields: List[str], parent=None, row_values: Optional[Dict[str, str]] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select by Field/Regex")
        self._fields = list(fields)
        self._row_values = dict(row_values or {})

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Field"))
        self.combo = QComboBox()
        self.combo.addItems(self._fields)
        row.addWidget(self.combo)
        root.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Regex"))
        self.regex = QLineEdit()
        self.regex.setPlaceholderText("e.g. .*\\iPhone\\2023\\02\\.*")
        row2.addWidget(self.regex)
        root.addLayout(row2)

        # Helper tips / mini manual
        tips = QLabel(
            "使用說明：依欄位+正則批次勾選或取消。\n"
            "- 精確比對：^文字$\n"
            "- 任意字串：.*\n"
            "- 數字：\\d+（例如檔名數字）\n"
            "範例：^IMG_\\d+\\.HEIC$（檔名），^H:\\\\Photos\\\\2023\\\\.*（資料夾）"
        )
        tips.setWordWrap(True)
        root.addWidget(tips)

        btns = QHBoxLayout()
        self.btn_select = QPushButton("Select")
        self.btn_unselect = QPushButton("Unselect")
        self.btn_close = QPushButton("Close")
        btns.addWidget(self.btn_select)
        btns.addWidget(self.btn_unselect)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        root.addLayout(btns)

        self.btn_close.clicked.connect(self.accept)
        self.btn_select.clicked.connect(self._emit_select)
        self.btn_unselect.clicked.connect(self._emit_unselect)

        # Defaults:
        # - Field => Folder
        # - Regex => exact of current row field if available; else blank
        self._set_default_field("Folder")
        self.combo.currentTextChanged.connect(self._on_field_changed)
        self._apply_exact_regex_for_current_field()

    def _emit_select(self) -> None:
        field = self.combo.currentText()
        pattern = self.regex.text()
        self.selectRequested.emit(field, pattern)

    def _emit_unselect(self) -> None:
        field = self.combo.currentText()
        pattern = self.regex.text()
        self.unselectRequested.emit(field, pattern)

    # Internals
    def _set_default_field(self, field_name: str) -> None:
        try:
            idx = self.combo.findText(field_name)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _on_field_changed(self, _text: str) -> None:
        # When switching field, update regex to exact match of that field value
        self._apply_exact_regex_for_current_field()

    def _apply_exact_regex_for_current_field(self) -> None:
        field = self.combo.currentText()
        value = self._row_values.get(field, "")
        if value:
            import re as _re
            self.regex.setText(f"^{_re.escape(value)}$")
        else:
            # Leave blank to keep placeholder when no data row highlighted
            self.regex.clear()


