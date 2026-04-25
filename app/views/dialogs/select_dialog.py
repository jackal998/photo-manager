"""Dialog for selecting items by field/regex."""

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

# Mirrors context_menu._SETTABLE_DECISIONS — kept here to avoid a circular import.
_SETTABLE_DECISIONS: list[tuple[str, str]] = [
    ("delete",               "delete"),
    ("keep (remove action)", ""),      # sets user_decision="" — clears any existing decision
]


class SelectDialog(QDialog):
    """Dialog to select/unselect rows by field+regex and optionally set an action.

    Signals:
        selectRequested(str, str): Emitted when user clicks Select.
        unselectRequested(str, str): Emitted when user clicks Unselect.
        setActionRequested(str, str, str): Emitted when user clicks Set Action
            (field, regex, action_value).
    """

    selectRequested = Signal(str, str)      # field, regex
    unselectRequested = Signal(str, str)    # field, regex
    setActionRequested = Signal(str, str, str)  # field, regex, action_value

    def __init__(
        self,
        fields: list[str],
        parent=None,
        row_values: dict[str, str] | None = None,
        initial_field: str | None = None,
    ) -> None:
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

        tips = QLabel(
            "Regex tips:\n"
            "  Exact match: ^text$     Any substring: .*     Digits: \\d+\n"
            "  Examples:  ^IMG_\\d+\\.HEIC$  (File Name),  ^delete$  (Action),\n"
            "             ^exact$  (Match),  ^H:\\\\Photos\\\\2023\\\\  (Folder)"
        )
        tips.setWordWrap(True)
        root.addWidget(tips)

        # ── Select / Unselect ──────────────────────────────────────────────
        btns = QHBoxLayout()
        self.btn_select = QPushButton("Select")
        self.btn_unselect = QPushButton("Unselect")
        btns.addWidget(self.btn_select)
        btns.addWidget(self.btn_unselect)
        btns.addStretch(1)
        root.addLayout(btns)

        # ── Set Action ─────────────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Set Action:"))
        self._action_combo = QComboBox()
        for label, _value in _SETTABLE_DECISIONS:
            self._action_combo.addItem(label)
        action_row.addWidget(self._action_combo)
        self._btn_set_action = QPushButton("Set Action")
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
        self.btn_select.clicked.connect(self._emit_select)
        self.btn_unselect.clicked.connect(self._emit_unselect)
        self._btn_set_action.clicked.connect(self._emit_set_action)

        # Default field: use initial_field if provided and valid, else "File Name"
        if initial_field and self.combo.findText(initial_field) >= 0:
            self._set_default_field(initial_field)
        else:
            self._set_default_field("File Name")
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

    def _emit_set_action(self) -> None:
        field = self.combo.currentText()
        pattern = self.regex.text()
        idx = self._action_combo.currentIndex()
        _label, value = _SETTABLE_DECISIONS[idx]
        self.setActionRequested.emit(field, pattern, value)

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
