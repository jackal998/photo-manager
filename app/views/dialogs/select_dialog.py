"""Dialog for setting action on items matching a field/regex."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from app.views.constants import SETTABLE_DECISIONS as _SETTABLE_DECISIONS


class ActionDialog(QDialog):
    """Dialog to set an action on rows matching a field+regex.

    Signals:
        setActionRequested(str, str, str): Emitted when user clicks Apply
            (field, regex, action_value).
    """

    setActionRequested = Signal(str, str, str)  # field, regex, action_value

    def __init__(
        self,
        fields: list[str],
        parent=None,
        row_values: dict[str, str] | None = None,
        initial_field: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Action by Field/Regex")
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

        tips = QLabel(
            "Regex tips:\n"
            "  Exact match: ^text$     Any substring: .*     Digits: \\d+\n"
            "  Examples:  ^IMG_\\d+\\.HEIC$  (File Name),  ^delete$  (Action),\n"
            "             ^exact$  (Similarity),  ^H:\\\\Photos\\\\2023\\\\  (Folder)"
        )
        tips.setWordWrap(True)
        root.addWidget(tips)

        # ── Set Action ─────────────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Set Action:"))
        self._action_combo = QComboBox()
        for label, _value in _SETTABLE_DECISIONS:
            self._action_combo.addItem(label)
        action_row.addWidget(self._action_combo)
        self._btn_set_action = QPushButton("Apply")
        action_row.addWidget(self._btn_set_action)
        action_row.addStretch(1)
        root.addLayout(action_row)

        # ── Close ──────────────────────────────────────────────────────────
        close_row = QHBoxLayout()
        self.btn_close = QPushButton("Close")
        close_row.addStretch(1)
        close_row.addWidget(self.btn_close)
        root.addLayout(close_row)

        self.btn_close.clicked.connect(self.accept)
        self._btn_set_action.clicked.connect(self._emit_set_action)

        if initial_field and self.combo.findText(initial_field) >= 0:
            self._set_default_field(initial_field)
        else:
            self._set_default_field("File Name")
        self.combo.currentTextChanged.connect(self._on_field_changed)
        self._apply_exact_regex_for_current_field()

    def _emit_set_action(self) -> None:
        field = self.combo.currentText()
        pattern = self.regex.text()
        idx = self._action_combo.currentIndex()
        _label, value = _SETTABLE_DECISIONS[idx]
        self.setActionRequested.emit(field, pattern, value)

    def _set_default_field(self, field_name: str) -> None:
        try:
            idx = self.combo.findText(field_name)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _on_field_changed(self, _text: str) -> None:
        self._apply_exact_regex_for_current_field()

    def _apply_exact_regex_for_current_field(self) -> None:
        field = self.combo.currentText()
        value = self._row_values.get(field, "")
        if value:
            import re as _re
            self.regex.setText(f"^{_re.escape(value)}$")
        else:
            self.regex.clear()


# Backward-compatibility alias
SelectDialog = ActionDialog
