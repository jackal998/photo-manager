"""Dialog for setting action on items matching a field/regex."""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
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

MODE_BEGINNER = "beginner"
MODE_REGEX = "regex"

# Beginner-mode operator → (translation_key, regex-builder closure).
# The closure receives the user's plain text and returns the regex
# pattern that drives the live preview + Apply path. `re.escape` keeps
# the input literal — e.g. typing "IMG_001.jpg (copy)" works without
# the user needing to know that ()/. are special.
_BEGINNER_OPS: list[tuple[str, str, Callable[[str], str]]] = [
    ("contains",    "action_dialog.beginner_op_contains",    lambda txt: re.escape(txt)),
    ("starts_with", "action_dialog.beginner_op_starts_with", lambda txt: "^" + re.escape(txt)),
    ("ends_with",   "action_dialog.beginner_op_ends_with",   lambda txt: re.escape(txt) + "$"),
    ("exact",       "action_dialog.beginner_op_exact",       lambda txt: "^" + re.escape(txt) + "$"),
]

# Cheatsheet chip rows: each is (insertion_text, translation_key for
# label+tooltip). Click on a chip inserts the text at the regex line
# edit's caret position. Only shown in Regex mode.
_CHEATSHEET_TOKENS: list[tuple[str, str]] = [
    (".*",    "action_dialog.cheatsheet_any"),
    ("\\d",   "action_dialog.cheatsheet_digit"),
    ("\\w",   "action_dialog.cheatsheet_word"),
    ("^",     "action_dialog.cheatsheet_start"),
    ("$",     "action_dialog.cheatsheet_end"),
    ("\\.",   "action_dialog.cheatsheet_dot"),
    ("[abc]", "action_dialog.cheatsheet_set"),
]

# Recent-patterns persistence: dotted path under JsonSettings + cap.
# Cap chosen so the dropdown stays scannable without scroll on a
# typical screen.
_RECENT_KEY = "ui.action_dialog.recent_patterns"
_MODE_KEY = "ui.action_dialog.mode"
_RECENT_CAP = 10


def _field_display(name: str) -> str:
    """Return the localized label for an internal field name."""
    key = _FIELD_LABEL_KEYS.get(name)
    return t(key) if key else name


class _MatchHighlightDelegate(QStyledItemDelegate):
    """Render preview-list rows with the regex match span emboldened.

    The match span for each row is stashed by ``_refresh_preview`` on
    item.data(Qt.UserRole) as a (start, end) tuple of character
    offsets. Without a stored span (or with start>=end), the row falls
    back to the default rendering — keeps the placeholder ("No matches")
    and pre-highlight states clean.

    Custom paint splits the visible text into [pre, match, post] and
    paints each segment with the same QPalette colours but with bold on
    the match. Sticking to the default brushes (rather than a coloured
    highlight) keeps light/dark themes both readable without hard-coded
    style choices.
    """

    def paint(  # noqa: D401 — Qt override
        self,
        painter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        text = index.data(Qt.DisplayRole) or ""
        span = index.data(Qt.UserRole)
        if not span or not isinstance(span, tuple) or len(span) != 2:
            super().paint(painter, option, index)
            return
        start, end = int(span[0]), int(span[1])
        if start < 0 or end <= start or end > len(text):
            super().paint(painter, option, index)
            return

        # Initialise option from the style so selection / focus colours
        # apply as usual; we just override the text-rendering branch.
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # Clear text so the default style draws the row background +
        # frame without painting the unhighlighted text underneath.
        opt.text = ""
        widget = option.widget
        style = widget.style() if widget else opt.styleObject and opt.styleObject.style()
        if style is None:
            super().paint(painter, option, index)
            return
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        # Compute the text rect (where the default row text would draw).
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget)
        painter.save()
        painter.setClipRect(text_rect)
        painter.setPen(opt.palette.color(opt.palette.ColorGroup.Active,
                                          opt.palette.ColorRole.Text)
                       if opt.state & QStyle.State_Selected == 0
                       else opt.palette.color(opt.palette.ColorGroup.Active,
                                               opt.palette.ColorRole.HighlightedText))

        pre, mid, post = text[:start], text[start:end], text[end:]
        fm = painter.fontMetrics()
        x = text_rect.left()
        y = text_rect.top() + (text_rect.height() + fm.ascent() - fm.descent()) // 2

        for segment, bold in [(pre, False), (mid, True), (post, False)]:
            if not segment:
                continue
            font = QFont(painter.font())
            font.setBold(bold)
            painter.setFont(font)
            painter.drawText(x, y, segment)
            x += painter.fontMetrics().horizontalAdvance(segment)
        painter.restore()


class ActionDialog(QDialog):
    """Dialog to set an action on rows matching a field+regex.

    Signals:
        setActionRequested(str, str, str): Emitted when user clicks Apply
            (field, regex, action_value).

    Phase A added a live preview pane / counter / inline validator when
    a ``match_fn`` is supplied. Phase B layers on top:
      * Beginner / Regex mode toggle. Beginner replaces the regex line
        edit with "Find rows where it [contains | starts with | ends
        with | exactly matches] [text]" and builds the regex internally
        so non-regex users never type a ``\\d`` in their lives.
      * Cheatsheet chips below the regex row (Regex mode only) — click
        to insert tokens at the caret.
      * Recent-patterns dropdown next to the regex line edit, populated
        on Apply and persisted to settings.json across runs.
      * Match-span highlight on each preview list row so users can see
        WHY each row matched.

    With ``match_fn=None`` the dialog falls back to the original flat
    Regex-only layout — keeps existing callers, tests, and QA paths
    working unchanged.
    """

    setActionRequested = Signal(str, str, str)  # field, regex, action_value

    def __init__(
        self,
        fields: list[str],
        parent=None,
        row_values: dict[str, str] | None = None,
        initial_field: str | None = None,
        match_fn: MatchFn | None = None,
        settings: object | None = None,
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
        self._settings = settings
        self._sample_cap = 50  # mirrors build_match_fn default

        # Mode is only meaningful when match_fn is supplied (Beginner
        # mode would have nothing to live-preview against). Default is
        # Beginner — the on-ramp for non-regex users; power users who
        # prefer Regex flip the toggle once and the choice persists.
        if self._match_fn is None:
            self._mode = MODE_REGEX
        else:
            persisted = self._settings_get(_MODE_KEY, MODE_BEGINNER)
            self._mode = persisted if persisted in (MODE_BEGINNER, MODE_REGEX) else MODE_BEGINNER

        self._recent_patterns: list[str] = list(
            self._settings_get(_RECENT_KEY, []) or []
        )

        # Build the per-field combo box, regex line edit, action combo, and
        # buttons. They live in this `left_layout` regardless of whether
        # the preview pane is constructed — the preview pane just sits
        # next to them when present.
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # ── Mode toggle (only when match_fn is provided) ───────────────────
        if self._match_fn is not None:
            mode_row = QHBoxLayout()
            mode_row.addWidget(QLabel(t("action_dialog.mode_label")))
            self._mode_beginner_btn = QRadioButton(t("action_dialog.mode_beginner"))
            self._mode_beginner_btn.setObjectName("regexModeBeginner")
            self._mode_regex_btn = QRadioButton(t("action_dialog.mode_regex"))
            self._mode_regex_btn.setObjectName("regexModeRegex")
            mode_row.addWidget(self._mode_beginner_btn)
            mode_row.addWidget(self._mode_regex_btn)
            mode_row.addStretch(1)
            left_layout.addLayout(mode_row)
            mode_group = QButtonGroup(self)
            mode_group.addButton(self._mode_beginner_btn)
            mode_group.addButton(self._mode_regex_btn)
            self._mode_button_group = mode_group  # keep ref alive
            (self._mode_beginner_btn if self._mode == MODE_BEGINNER
             else self._mode_regex_btn).setChecked(True)
            self._mode_beginner_btn.toggled.connect(self._on_mode_toggled)

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

        # ── Beginner-mode row (Find rows where it [op] [text]) ─────────────
        # Built unconditionally so the mode toggle can show/hide it; sits
        # idle when not in Beginner mode.
        self._beginner_widget = QWidget()
        self._beginner_widget.setObjectName("regexBeginnerRow")
        beginner_layout = QHBoxLayout(self._beginner_widget)
        beginner_layout.setContentsMargins(0, 0, 0, 0)
        beginner_layout.addWidget(QLabel(t("action_dialog.beginner_prefix")))
        self._beginner_op_combo = QComboBox()
        self._beginner_op_combo.setObjectName("regexBeginnerOpCombo")
        for op_key, label_key, _builder in _BEGINNER_OPS:
            self._beginner_op_combo.addItem(t(label_key), userData=op_key)
        beginner_layout.addWidget(self._beginner_op_combo)
        self._beginner_text = QLineEdit()
        self._beginner_text.setObjectName("regexBeginnerText")
        self._beginner_text.setPlaceholderText(t("action_dialog.beginner_text_placeholder"))
        beginner_layout.addWidget(self._beginner_text, stretch=1)
        left_layout.addWidget(self._beginner_widget)

        # ── Regex-mode container (regex line edit + validation + counter +
        #    Recent button + cheatsheet chips + tips) ──────────────────────
        self._regex_widget = QWidget()
        self._regex_widget.setObjectName("regexRegexRow")
        regex_layout = QVBoxLayout(self._regex_widget)
        regex_layout.setContentsMargins(0, 0, 0, 0)

        regex_row = QHBoxLayout()
        regex_row.addWidget(QLabel(t("action_dialog.regex_label")))
        self.regex = QLineEdit()
        self.regex.setObjectName("regexLineEdit")
        self.regex.setPlaceholderText(t("action_dialog.regex_placeholder"))
        regex_row.addWidget(self.regex)

        self._validation_icon = QLabel("")
        self._validation_icon.setObjectName("regexValidationIcon")
        self._validation_icon.setFixedWidth(16)
        regex_row.addWidget(self._validation_icon)

        # Recent-patterns dropdown — click to pick a previous pattern.
        self._recent_btn = QPushButton(t("action_dialog.recent_button"))
        self._recent_btn.setObjectName("regexRecentButton")
        self._recent_btn.clicked.connect(self._show_recent_menu)
        regex_row.addWidget(self._recent_btn)
        regex_layout.addLayout(regex_row)

        # Friendly error string sits directly under the regex row, hidden
        # when the regex compiles. Coloring the label red is enough; we
        # don't restyle the QLineEdit border (focus-ring fights with the
        # native Windows style on PySide6).
        self._validation_error = QLabel("")
        self._validation_error.setObjectName("regexValidationError")
        self._validation_error.setStyleSheet("color: #d62728;")
        self._validation_error.setWordWrap(True)
        self._validation_error.hide()
        regex_layout.addWidget(self._validation_error)

        # Cheatsheet chips — click to insert a regex token at the caret.
        # Hidden when match_fn is None so legacy callers see today's
        # layout exactly.
        if self._match_fn is not None:
            chips_row = QHBoxLayout()
            chips_row.setContentsMargins(0, 0, 0, 0)
            chips_row.addWidget(QLabel(t("action_dialog.cheatsheet_label")))
            for token, label_key in _CHEATSHEET_TOKENS:
                chip = QPushButton(t(label_key).split(" — ")[0])
                chip.setObjectName(f"regexCheatsheet_{token}")
                chip.setToolTip(t(label_key))
                chip.setFlat(True)
                chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
                chip.clicked.connect(
                    lambda _checked=False, _tok=token: self._insert_token(_tok)
                )
                chips_row.addWidget(chip)
            chips_row.addStretch(1)
            regex_layout.addLayout(chips_row)

        # ── Tips ───────────────────────────────────────────────────────────
        tips = QLabel(t("action_dialog.regex_tips"))
        tips.setWordWrap(True)
        regex_layout.addWidget(tips)
        left_layout.addWidget(self._regex_widget)

        # ── Match counter row (visible in BOTH modes when match_fn) ────────
        # Lives outside the mode containers so toggling mode never hides
        # the live count — it's the primary feedback for both Beginner
        # and Regex inputs.
        counter_row = QHBoxLayout()
        counter_row.addStretch(1)
        self._match_counter = QLabel("")
        self._match_counter.setObjectName("regexMatchCounter")
        if self._match_fn is None:
            self._match_counter.hide()
        counter_row.addWidget(self._match_counter)
        left_layout.addLayout(counter_row)

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
            splitter.setSizes([420, 380])
            root.addWidget(splitter)
            self.setMinimumSize(820, 500)
            self.resize(880, 540)
        else:
            root.addWidget(left_widget)
            left_layout.setContentsMargins(11, 11, 11, 11)

        self.btn_close.clicked.connect(self.accept)
        self._btn_set_action.clicked.connect(self._emit_set_action)

        if initial_field and self.combo.findData(initial_field) >= 0:
            self._set_default_field(initial_field)
        else:
            self._set_default_field("File Name")
        self.combo.currentIndexChanged.connect(self._on_field_changed)

        # Live validation on the regex input. Beginner-mode inputs go
        # through _build_pattern_for_preview which is always-valid, so
        # we don't need a parallel validator there — the icon hides.
        self.regex.textChanged.connect(self._validate_regex)
        if self._match_fn is not None:
            # Both modes share the same debounce: any user input retriggers
            # the live preview. Keystrokes from either the regex line edit
            # or the Beginner text box, plus combo changes, all funnel
            # through the same timer to avoid running the closure on every
            # character.
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(150)
            self._preview_timer.timeout.connect(self._refresh_preview)
            self.regex.textChanged.connect(self._preview_timer.start)
            self._beginner_text.textChanged.connect(self._preview_timer.start)
            self._beginner_op_combo.currentIndexChanged.connect(self._preview_timer.start)
            self.combo.currentIndexChanged.connect(self._preview_timer.start)

        self._apply_exact_regex_for_current_field()
        # Default beginner op is "contains" (index 0) — most useful
        # starting state and matches the most-common user intent.
        self._beginner_op_combo.setCurrentIndex(0)
        # Apply the mode visibility AFTER all widgets exist.
        self._apply_mode_visibility()
        self._validate_regex()
        if self._match_fn is not None:
            self._refresh_preview()

    # ── Settings helpers ───────────────────────────────────────────────────

    def _settings_get(self, key: str, default):
        if self._settings is None:
            return default
        try:
            return self._settings.get(key, default)
        except Exception:
            return default

    def _settings_set(self, key: str, value) -> None:
        if self._settings is None:
            return
        try:
            self._settings.set(key, value)
            self._settings.save()
        except Exception:
            pass

    # ── Mode toggle ────────────────────────────────────────────────────────

    def _on_mode_toggled(self, checked_beginner: bool) -> None:
        # The radio group fires twice on a switch (one off, one on); we
        # only need to act on the True side so the apply runs once.
        if not checked_beginner and self._mode_regex_btn.isChecked():
            self._mode = MODE_REGEX
        elif checked_beginner:
            self._mode = MODE_BEGINNER
        else:
            return
        self._settings_set(_MODE_KEY, self._mode)
        self._apply_mode_visibility()
        self._validate_regex()
        if self._match_fn is not None:
            self._refresh_preview()

    def _apply_mode_visibility(self) -> None:
        beginner_visible = self._mode == MODE_BEGINNER and self._match_fn is not None
        regex_visible = not beginner_visible
        self._beginner_widget.setVisible(beginner_visible)
        self._regex_widget.setVisible(regex_visible)

    # ── Pattern build (mode-aware) ─────────────────────────────────────────

    def _build_pattern(self) -> str:
        """Return the regex pattern that drives both preview and Apply.

        Beginner mode synthesises a regex from (operator, plain text) so
        the user never sees a backslash. Regex mode passes through what
        the user typed.
        """
        if self._mode == MODE_BEGINNER and self._match_fn is not None:
            text = self._beginner_text.text()
            if not text:
                return ""
            op_key = self._beginner_op_combo.currentData() or "contains"
            for k, _label_key, builder in _BEGINNER_OPS:
                if k == op_key:
                    return builder(text)
            return re.escape(text)
        return self.regex.text()

    # ── Cheatsheet ─────────────────────────────────────────────────────────

    def _insert_token(self, token: str) -> None:
        """Insert a regex token at the regex line edit's caret position."""
        self.regex.setFocus()
        cur = self.regex.cursorPosition()
        text = self.regex.text()
        new_text = text[:cur] + token + text[cur:]
        self.regex.setText(new_text)
        self.regex.setCursorPosition(cur + len(token))

    # ── Recent patterns ────────────────────────────────────────────────────

    def _show_recent_menu(self) -> None:
        menu = QMenu(self)
        if not self._recent_patterns:
            empty = menu.addAction(t("action_dialog.recent_empty"))
            empty.setEnabled(False)
        else:
            for pat in self._recent_patterns:
                act = menu.addAction(pat)
                act.triggered.connect(
                    lambda _checked=False, _pat=pat: self._apply_recent_pattern(_pat)
                )
            menu.addSeparator()
            clear_act = menu.addAction(t("action_dialog.recent_clear"))
            clear_act.triggered.connect(self._clear_recent_patterns)
        # Position the menu just below the Recent button.
        pos = self._recent_btn.mapToGlobal(QPoint(0, self._recent_btn.height()))
        menu.exec(pos)

    def _apply_recent_pattern(self, pattern: str) -> None:
        # Picking from Recent always lands the user in Regex mode — the
        # stored patterns are raw regex strings, not Beginner tuples.
        if self._mode != MODE_REGEX and self._match_fn is not None:
            self._mode_regex_btn.setChecked(True)
        self.regex.setText(pattern)

    def _clear_recent_patterns(self) -> None:
        self._recent_patterns = []
        self._settings_set(_RECENT_KEY, [])

    def _record_recent_pattern(self, pattern: str) -> None:
        if not pattern:
            return
        # Most-recent first, deduped, capped. The cap keeps the dropdown
        # scannable and bounds settings.json growth.
        existing = [p for p in self._recent_patterns if p != pattern]
        self._recent_patterns = ([pattern] + existing)[:_RECENT_CAP]
        self._settings_set(_RECENT_KEY, self._recent_patterns)

    # ── Preview pane (only built when match_fn is supplied) ────────────────

    def _build_preview_pane(self) -> QWidget:
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_layout.addWidget(QLabel(t("action_dialog.preview_label")))

        self._preview_list = QListWidget()
        self._preview_list.setObjectName("regexPreviewList")
        self._preview_list.setItemDelegate(_MatchHighlightDelegate(self._preview_list))
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

        In Beginner mode the synthesised pattern is always valid (we
        re.escape the user's input), so the icon stays empty and we
        hide the error label.

        Empty regex → no icon, no error (neutral state). Valid regex →
        green ✓, error hidden. Invalid regex → red ✗, error shown with
        the `re.error` message. The match counter falls back to an em
        dash while the regex is invalid.
        """
        if self._mode == MODE_BEGINNER:
            self._validation_icon.setText("")
            self._validation_icon.setStyleSheet("")
            self._validation_icon.setAccessibleName("")
            self._validation_error.hide()
            return

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
        """Pull live counts + sample names from the injected match_fn.

        Both modes funnel through this — the only difference is which
        widget produced the pattern (Regex mode uses self.regex, Beginner
        mode synthesises via _build_pattern). Match-span highlighting is
        applied per-row by storing (start, end) on each list item; the
        delegate paints from there.
        """
        if self._match_fn is None:
            return

        pattern = self._build_pattern()
        # Only run the closure for syntactically-valid patterns; the
        # validator already updated the counter to "—" for invalid ones.
        if pattern:
            try:
                rx = re.compile(pattern, re.IGNORECASE)
            except re.error:
                self._preview_list.clear()
                self._preview_truncated.hide()
                return
        else:
            rx = None

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
                item = QListWidgetItem(name)
                if rx is not None:
                    m = rx.search(name)
                    if m is not None:
                        item.setData(Qt.UserRole, (m.start(), m.end()))
                self._preview_list.addItem(item)

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
        pattern = self._build_pattern()
        # Record only the raw pattern (not the Beginner tuple) — keeps
        # the recent list usable from either mode and survives mode
        # toggles. Empty patterns aren't worth keeping.
        self._record_recent_pattern(pattern)
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
