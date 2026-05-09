"""Dialog for setting action on items matching a field/regex."""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.views.constants import settable_decisions
from infrastructure.i18n import t

# Maps the internal English field name (used as the lookup key in
# regex matching and column dispatch) to its column.* translation key.
# The dialog displays the translated label but emits the English name
# in setActionRequested so downstream regex matchers stay locale-free.
_FIELD_LABEL_KEYS: dict[str, str] = {
    "Similarity":    "column.similarity",
    "Action":        "column.action",
    "File Name":     "column.file_name",
    "Folder":        "column.folder",
    "Size (Bytes)":  "column.size_bytes",
    "Group Count":   "column.group_count",
    "Creation Date": "column.creation_date",
    "Shot Date":     "column.shot_date",
}

# Type alias for the live-preview match function. Callers (DialogHandler
# from MainWindow, ExecuteActionDialog inline) build this via
# `app.views.handlers.file_operations.build_match_fn` and pass it in.
# Returns (matched_count, total_count, sample_basenames). Sample list is
# bounded by build_match_fn's sample_cap; matched count is the full total.
MatchFn = Callable[[str, str], tuple[int, int, list[str]]]


def _field_display(name: str) -> str:
    """Return the localized label for an internal field name."""
    key = _FIELD_LABEL_KEYS.get(name)
    return t(key) if key else name


class ActionDialog(QDialog):
    """Dialog to set an action on rows matching a field+regex.

    Signals:
        setActionRequested(str, str, str): Emitted when user clicks Apply
            (field, regex, action_value).

    When a `match_fn` is supplied the dialog grows a right-hand preview
    pane that updates live (debounced 150 ms) so the user can see how
    many rows will be affected before clicking Apply, plus an inline ✓/✗
    validator that surfaces `re.error` immediately. With `match_fn=None`
    the dialog falls back to the original flat layout — keeps existing
    callers, tests, and QA paths working unchanged.
    """

    setActionRequested = Signal(str, str, str)  # field, regex, action_value

    def __init__(
        self,
        fields: list[str],
        parent=None,
        row_values: dict[str, str] | None = None,
        initial_field: str | None = None,
        match_fn: MatchFn | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("action_dialog.title"))
        # #139 — explicit ApplicationModal so OS-level click events on
        # the parent (e.g. main window menu bar) are blocked while this
        # dialog is up. QDialog.exec() alone doesn't do this; see the
        # ExecuteActionDialog comment for the full reasoning.
        self.setWindowModality(Qt.ApplicationModal)
        self._fields = list(fields)
        self._row_values = dict(row_values or {})
        self._match_fn = match_fn
        self._sample_cap = 50  # mirrors build_match_fn default

        # Build the per-field combo box, regex line edit, action combo, and
        # buttons. They live in this `left_layout` regardless of whether
        # the preview pane is constructed — the preview pane just sits
        # next to them when present.
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # ── Field row ──────────────────────────────────────────────────────
        row = QHBoxLayout()
        row.addWidget(QLabel(t("action_dialog.field_label")))
        self.combo = QComboBox()
        self.combo.setObjectName("regexFieldCombo")
        # Display localized label; carry the English internal name as
        # itemData so currentField() always returns the lookup key.
        for fname in self._fields:
            self.combo.addItem(_field_display(fname), userData=fname)
        row.addWidget(self.combo)
        left_layout.addLayout(row)

        # ── Regex row + ✓/✗ icon + match counter ───────────────────────────
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(t("action_dialog.regex_label")))
        self.regex = QLineEdit()
        self.regex.setObjectName("regexLineEdit")
        self.regex.setPlaceholderText(t("action_dialog.regex_placeholder"))
        row2.addWidget(self.regex)

        self._validation_icon = QLabel("")
        self._validation_icon.setObjectName("regexValidationIcon")
        self._validation_icon.setFixedWidth(16)
        row2.addWidget(self._validation_icon)

        self._match_counter = QLabel("")
        self._match_counter.setObjectName("regexMatchCounter")
        # Hidden when match_fn is None — no records to count against.
        if self._match_fn is None:
            self._match_counter.hide()
        row2.addWidget(self._match_counter)
        left_layout.addLayout(row2)

        # Friendly error string sits directly under the regex row, hidden
        # when the regex compiles. Coloring the label red is enough; we
        # don't restyle the QLineEdit border (focus-ring fights with the
        # native Windows style on PySide6).
        self._validation_error = QLabel("")
        self._validation_error.setObjectName("regexValidationError")
        self._validation_error.setStyleSheet("color: #d62728;")
        self._validation_error.setWordWrap(True)
        self._validation_error.hide()
        left_layout.addWidget(self._validation_error)

        # ── Tips ───────────────────────────────────────────────────────────
        tips = QLabel(t("action_dialog.regex_tips"))
        tips.setWordWrap(True)
        left_layout.addWidget(tips)

        # ── Set Action ─────────────────────────────────────────────────────
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel(t("action_dialog.set_action_label")))
        self._action_combo = QComboBox()
        self._action_combo.setObjectName("regexActionCombo")
        # include_remove=True surfaces "remove from list" alongside the
        # decision options. The receiving handler routes the sentinel
        # value to the remove-from-review backend instead of the
        # user_decision update path.
        self._decisions = settable_decisions(include_remove=True)
        for label, _value in self._decisions:
            self._action_combo.addItem(label)
        action_row.addWidget(self._action_combo)
        self._btn_set_action = QPushButton(t("action_dialog.apply_button"))
        self._btn_set_action.setObjectName("regexApplyButton")
        action_row.addWidget(self._btn_set_action)
        action_row.addStretch(1)
        left_layout.addLayout(action_row)

        # ── Close ──────────────────────────────────────────────────────────
        close_row = QHBoxLayout()
        self.btn_close = QPushButton(t("action_dialog.close_button"))
        close_row.addStretch(1)
        close_row.addWidget(self.btn_close)
        left_layout.addLayout(close_row)

        # ── Compose root layout ────────────────────────────────────────────
        # Two shapes: with preview (QSplitter holding left + right panes)
        # and without (flat layout — left widget is the whole dialog body).
        # Tests and QA scenarios that don't pass match_fn see the original
        # shape, so their UIA paths and findChild lookups stay valid.
        root = QVBoxLayout(self)
        if self._match_fn is not None:
            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(left_widget)
            splitter.addWidget(self._build_preview_pane())
            splitter.setSizes([380, 380])
            root.addWidget(splitter)
            self.setMinimumSize(750, 450)
            self.resize(820, 480)
        else:
            root.addWidget(left_widget)
            # Strip nested-widget margin so the unwrapped form looks the
            # same as the original flat dialog.
            left_layout.setContentsMargins(11, 11, 11, 11)

        self.btn_close.clicked.connect(self.accept)
        self._btn_set_action.clicked.connect(self._emit_set_action)

        if initial_field and self.combo.findData(initial_field) >= 0:
            self._set_default_field(initial_field)
        else:
            self._set_default_field("File Name")
        self.combo.currentIndexChanged.connect(self._on_field_changed)

        # Live validation runs synchronously — `re.compile` is microseconds.
        # Preview runs through a debounce timer so we don't iterate the
        # full record set on every keystroke.
        self.regex.textChanged.connect(self._validate_regex)
        if self._match_fn is not None:
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(150)
            self._preview_timer.timeout.connect(self._refresh_preview)
            self.regex.textChanged.connect(self._preview_timer.start)
            self.combo.currentIndexChanged.connect(self._preview_timer.start)

        self._apply_exact_regex_for_current_field()
        # Apply may have set/cleared the regex synthetically; run an
        # initial validation pass so the icon/counter are coherent before
        # the user types anything.
        self._validate_regex()
        if self._match_fn is not None:
            self._refresh_preview()

    # ── Preview pane (only built when match_fn is supplied) ────────────────

    def _build_preview_pane(self) -> QWidget:
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_layout.addWidget(QLabel(t("action_dialog.preview_label")))

        self._preview_list = QListWidget()
        self._preview_list.setObjectName("regexPreviewList")
        right_layout.addWidget(self._preview_list, stretch=1)

        self._preview_truncated = QLabel("")
        self._preview_truncated.setObjectName("regexPreviewTruncated")
        self._preview_truncated.setStyleSheet("color: #555; font-style: italic;")
        self._preview_truncated.hide()
        right_layout.addWidget(self._preview_truncated)

        return right_widget

    # ── Validation (synchronous) ───────────────────────────────────────────

    def _validate_regex(self) -> None:
        """Update ✓/✗ icon and the friendly error label.

        Empty regex → no icon, no error (neutral state). Valid regex →
        green ✓, error hidden. Invalid regex → red ✗, error shown with
        the `re.error` message. The match counter falls back to an em
        dash while the regex is invalid.
        """
        pattern = self.regex.text()
        if not pattern:
            self._validation_icon.setText("")
            self._validation_icon.setStyleSheet("")
            self._validation_icon.setAccessibleName("")
            self._validation_error.hide()
            return

        try:
            re.compile(pattern)
        except re.error as exc:
            self._validation_icon.setText("✗")
            self._validation_icon.setStyleSheet("color: #d62728; font-weight: bold;")
            self._validation_icon.setAccessibleName(
                f"Regex invalid: {exc}"
            )
            self._validation_error.setText(
                t("action_dialog.invalid_regex").format(error=str(exc))
            )
            self._validation_error.show()
            if self._match_fn is not None:
                self._match_counter.setText(
                    t("action_dialog.match_counter_invalid")
                )
            return

        self._validation_icon.setText("✓")
        self._validation_icon.setStyleSheet("color: #2ca02c; font-weight: bold;")
        self._validation_icon.setAccessibleName("Regex valid")
        self._validation_error.hide()

    # ── Preview (debounced) ────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        """Pull live counts + sample names from the injected match_fn."""
        if self._match_fn is None:
            return

        pattern = self.regex.text()
        # Only run the closure for syntactically-valid patterns; the
        # validator already updated the counter to "—" for invalid ones.
        if pattern:
            try:
                re.compile(pattern)
            except re.error:
                self._preview_list.clear()
                self._preview_truncated.hide()
                return

        field = self._current_field()
        matched, total, samples = self._match_fn(field, pattern)

        self._match_counter.setText(
            t("action_dialog.match_counter").format(matched=matched, total=total)
        )

        self._preview_list.clear()
        if matched == 0:
            self._preview_list.addItem(t("action_dialog.preview_empty"))
        else:
            for name in samples:
                self._preview_list.addItem(name)

        if matched > len(samples):
            self._preview_truncated.setText(
                t("action_dialog.preview_truncated").format(
                    n=matched - len(samples)
                )
            )
            self._preview_truncated.show()
        else:
            self._preview_truncated.hide()

    # ── Existing API ───────────────────────────────────────────────────────

    def _current_field(self) -> str:
        """Return the active English field name (lookup key, not the displayed label)."""
        data = self.combo.currentData()
        return str(data) if data is not None else self.combo.currentText()

    def _emit_set_action(self) -> None:
        field = self._current_field()
        pattern = self.regex.text()
        idx = self._action_combo.currentIndex()
        _label, value = self._decisions[idx]
        self.setActionRequested.emit(field, pattern, value)

    def _set_default_field(self, field_name: str) -> None:
        try:
            idx = self.combo.findData(field_name)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _on_field_changed(self, _index: int) -> None:
        self._apply_exact_regex_for_current_field()

    def _apply_exact_regex_for_current_field(self) -> None:
        field = self._current_field()
        value = self._row_values.get(field, "")
        if value:
            self.regex.setText(f"^{re.escape(value)}$")
        else:
            self.regex.clear()


# Backward-compatibility alias
SelectDialog = ActionDialog
