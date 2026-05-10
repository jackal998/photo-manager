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
    QGridLayout,
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

MODE_SIMPLE = "simple"
MODE_REGEX = "regex"
# Phase B persisted "beginner" as the mode value; Phase C renamed the
# user-facing label to "Simple" and the persisted value to "simple".
# We accept the legacy spelling on read so a user who upgrades doesn't
# silently flip back to the default.
_LEGACY_MODE_VALUES = {"beginner": MODE_SIMPLE, "simple": MODE_SIMPLE, "regex": MODE_REGEX}

# Simple-mode operator → (translation_key, regex-builder closure).
# The closure receives the user's plain text and returns the regex
# pattern that drives the live preview + Apply path. `re.escape` keeps
# the input literal — e.g. typing "IMG_001.jpg (copy)" works without
# the user needing to know that ()/. are special.
_SIMPLE_OPS: list[tuple[str, str, Callable[[str], str]]] = [
    ("contains",    "action_dialog.simple_op_contains",    lambda txt: re.escape(txt)),
    ("starts_with", "action_dialog.simple_op_starts_with", lambda txt: "^" + re.escape(txt)),
    ("ends_with",   "action_dialog.simple_op_ends_with",   lambda txt: re.escape(txt) + "$"),
    ("exact",       "action_dialog.simple_op_exact",       lambda txt: "^" + re.escape(txt) + "$"),
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


def _is_plain_or_escaped(text: str) -> bool:
    """Return True if ``text`` consists only of literal characters
    (possibly escaped via backslash) — i.e. could have come from
    re.escape() applied to plain user input.

    The check: re.escape(decoded) round-trips back to the input. That
    catches ``\\.`` / ``\\(`` / ``\\\\`` etc. as plain-equivalent and
    rejects anything with quantifiers, alternation, character classes,
    lookarounds, or unescaped metacharacters.
    """
    # Decode escapes the same way Simple-mode would have produced them.
    # We can't use a stdlib unescape (none exists), so do a minimal walk:
    # backslash + char → char; everything else → itself; reject a
    # trailing lone backslash.
    decoded_chars: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            if i + 1 >= len(text):
                return False  # dangling backslash isn't from re.escape
            decoded_chars.append(text[i + 1])
            i += 2
        else:
            decoded_chars.append(c)
            i += 1
    decoded = "".join(decoded_chars)
    return re.escape(decoded) == text


def _try_parse_simple(pattern: str) -> tuple[str, str] | None:
    """Reverse-parse a regex into a Simple-mode (op_key, plain_text) pair.

    Returns ``None`` for any pattern Simple cannot represent — caller
    shows the "complex pattern" notice and disables the Simple inputs
    rather than silently dropping the user's expression.

    The four parseable shapes mirror ``_SIMPLE_OPS``:
      - ``^X$`` → ("exact", X)
      - ``^X``  → ("starts_with", X)   (X must NOT end with un-escaped $)
      - ``X$``  → ("ends_with", X)     (X must NOT start with un-escaped ^)
      - ``X``   → ("contains", X)
    where X is "plain or escaped" per ``_is_plain_or_escaped``.
    Empty pattern is accepted as ("contains", "") — matches the
    reset state when the user clears Simple's text input.
    """
    if pattern == "":
        return ("contains", "")

    has_caret = pattern.startswith("^")
    # A trailing un-escaped $ — count preceding backslashes; even count
    # means the $ is unescaped (and so anchors).
    has_dollar = False
    if pattern.endswith("$"):
        # walk back to count consecutive backslashes before the final $
        bs = 0
        idx = len(pattern) - 2
        while idx >= 0 and pattern[idx] == "\\":
            bs += 1
            idx -= 1
        has_dollar = bs % 2 == 0

    body = pattern
    if has_caret:
        body = body[1:]
    if has_dollar:
        body = body[:-1]

    if not _is_plain_or_escaped(body):
        return None

    # Decode the body back to the plain string the user would type.
    decoded: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            decoded.append(body[i + 1])
            i += 2
        else:
            decoded.append(body[i])
            i += 1
    text = "".join(decoded)

    if has_caret and has_dollar:
        return ("exact", text)
    if has_caret:
        return ("starts_with", text)
    if has_dollar:
        return ("ends_with", text)
    return ("contains", text)


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
        # Parens around the bitwise check matter — Python parses
        # `state & SELECTED == 0` as `state & (SELECTED == 0)` because
        # `==` binds tighter than `&`. Without them every row picks
        # HighlightedText (white on most themes), making the preview
        # list look "empty" against its white background.
        is_selected = bool(opt.state & QStyle.State_Selected)
        text_role = (opt.palette.ColorRole.HighlightedText if is_selected
                     else opt.palette.ColorRole.Text)
        painter.setPen(opt.palette.color(opt.palette.ColorGroup.Active, text_role))

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

        # Mode is only meaningful when match_fn is supplied (Simple
        # mode would have nothing to live-preview against). Default is
        # Simple — the on-ramp for non-regex users; power users who
        # prefer Regex flip the toggle once and the choice persists.
        # _LEGACY_MODE_VALUES translates the Phase B "beginner" string
        # so users who upgraded from Phase B don't silently flip back
        # to the default.
        if self._match_fn is None:
            self._mode = MODE_REGEX
        else:
            persisted = self._settings_get(_MODE_KEY, MODE_SIMPLE)
            self._mode = _LEGACY_MODE_VALUES.get(persisted, MODE_SIMPLE)

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
            self._mode_simple_btn = QRadioButton(t("action_dialog.mode_simple"))
            self._mode_simple_btn.setObjectName("regexModeSimple")
            self._mode_regex_btn = QRadioButton(t("action_dialog.mode_regex"))
            self._mode_regex_btn.setObjectName("regexModeRegex")
            mode_row.addWidget(self._mode_simple_btn)
            mode_row.addWidget(self._mode_regex_btn)
            mode_row.addStretch(1)
            left_layout.addLayout(mode_row)
            mode_group = QButtonGroup(self)
            mode_group.addButton(self._mode_simple_btn)
            mode_group.addButton(self._mode_regex_btn)
            self._mode_button_group = mode_group  # keep ref alive
            (self._mode_simple_btn if self._mode == MODE_SIMPLE
             else self._mode_regex_btn).setChecked(True)
            self._mode_simple_btn.toggled.connect(self._on_mode_toggled)

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

        # ── Simple-mode container ──────────────────────────────────────────
        # A vertical stack: inputs row (prefix label + op combo + text edit)
        # plus a complex-pattern notice that appears when the user toggles
        # to Simple while holding a regex Simple can't reverse-parse. The
        # notice keeps the regex line edit's value intact — only the Simple
        # display gives up; toggling back to Regex restores everything.
        self._simple_widget = QWidget()
        self._simple_widget.setObjectName("regexSimpleRow")
        simple_outer = QVBoxLayout(self._simple_widget)
        simple_outer.setContentsMargins(0, 0, 0, 0)
        simple_inputs_row = QHBoxLayout()
        simple_inputs_row.addWidget(QLabel(t("action_dialog.simple_prefix")))
        self._simple_op_combo = QComboBox()
        self._simple_op_combo.setObjectName("regexSimpleOpCombo")
        for op_key, label_key, _builder in _SIMPLE_OPS:
            self._simple_op_combo.addItem(t(label_key), userData=op_key)
        simple_inputs_row.addWidget(self._simple_op_combo)
        self._simple_text = QLineEdit()
        self._simple_text.setObjectName("regexSimpleText")
        self._simple_text.setPlaceholderText(t("action_dialog.simple_text_placeholder"))
        simple_inputs_row.addWidget(self._simple_text, stretch=1)
        simple_outer.addLayout(simple_inputs_row)
        self._simple_complex_notice = QLabel(t("action_dialog.simple_complex_notice"))
        self._simple_complex_notice.setObjectName("regexSimpleComplexNotice")
        self._simple_complex_notice.setStyleSheet("color: #a86200;")  # amber
        self._simple_complex_notice.setWordWrap(True)
        self._simple_complex_notice.hide()
        simple_outer.addWidget(self._simple_complex_notice)
        left_layout.addWidget(self._simple_widget)

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

        # Cheatsheet — 3-column grid of (token button, description) pairs.
        # Vertical stack of 7 rows used too much height; 3 columns lets
        # all 7 tokens fit in 3 rows. Each "column block" is two grid
        # columns wide: one for the button, one for the description, so
        # buttons stay aligned even when descriptions vary in length.
        if self._match_fn is not None:
            chips_header = QLabel(t("action_dialog.cheatsheet_label"))
            chips_header.setStyleSheet("color: #555;")
            regex_layout.addWidget(chips_header)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(4)
            cols = 3
            for idx, (token, label_key) in enumerate(_CHEATSHEET_TOKENS):
                row_idx = idx // cols
                col_block = idx % cols
                chip = QPushButton(token)
                chip.setObjectName(f"regexCheatsheet_{token}")
                chip.setToolTip(t(label_key))
                chip.setFixedWidth(64)
                chip.clicked.connect(
                    lambda _checked=False, _tok=token: self._insert_token(_tok)
                )
                grid.addWidget(chip, row_idx, col_block * 2)
                # Description: the localized label minus the leading
                # "TOKEN — " prefix that the en/zh_TW values both carry.
                full = t(label_key)
                desc_text = full.split(" — ", 1)[-1] if " — " in full else full
                desc = QLabel(desc_text)
                desc.setStyleSheet("color: #555;")
                grid.addWidget(desc, row_idx, col_block * 2 + 1)
            # Make description columns stretchable so chips stay tight
            # against their description but the row uses available width.
            for col in range(cols):
                grid.setColumnStretch(col * 2 + 1, 1)
            regex_layout.addLayout(grid)
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
            # Shrunk after dropping the wrapped tips paragraph and
            # restructuring chips into compact button-with-aside-label
            # rows. The dialog used to feel "too tall" — particularly
            # in Beginner mode where the regex/chip section is hidden.
            self.setMinimumSize(720, 380)
            self.resize(780, 420)
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

        # Live validation on the regex input. Simple mode synthesises
        # patterns via re.escape so they're always valid — the validator
        # short-circuits in that branch and the icon stays empty.
        self.regex.textChanged.connect(self._validate_regex)
        if self._match_fn is not None:
            # Phase C: Simple-mode inputs write through to self.regex
            # immediately so the regex line edit is the single source of
            # truth across modes. _writeto_regex_from_simple guards
            # against feedback loops by blocking signals before setText.
            # The preview timer + validator listen to self.regex only;
            # the Simple→regex write flows naturally through that.
            self._simple_text.textChanged.connect(self._writeto_regex_from_simple)
            self._simple_op_combo.currentIndexChanged.connect(self._writeto_regex_from_simple)

            # Both modes share the same debounce: any user input
            # retriggers the live preview. self.regex.textChanged covers
            # both Regex-mode keystrokes AND the Simple write-through.
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(150)
            self._preview_timer.timeout.connect(self._refresh_preview)
            self.regex.textChanged.connect(self._preview_timer.start)
            self.combo.currentIndexChanged.connect(self._preview_timer.start)

        self._apply_exact_regex_for_current_field()
        # Default Simple op is "contains" (index 0) — most useful
        # starting state and matches the most-common user intent.
        self._simple_op_combo.setCurrentIndex(0)
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

    def _on_mode_toggled(self, checked_simple: bool) -> None:
        # The radio group fires twice on a switch (one off, one on); we
        # only need to act on the True side so the apply runs once.
        if not checked_simple and self._mode_regex_btn.isChecked():
            self._mode = MODE_REGEX
        elif checked_simple:
            self._mode = MODE_SIMPLE
        else:
            return
        self._settings_set(_MODE_KEY, self._mode)
        self._apply_mode_visibility()
        self._validate_regex()
        if self._match_fn is not None:
            self._refresh_preview()

    def _apply_mode_visibility(self) -> None:
        """Show/hide the mode containers and reverse-parse on entering Simple.

        Phase C invariant: ``self.regex.text()`` is the single source of
        truth across both modes. Switching to Simple tries to populate
        the Simple inputs from the current regex via ``_try_parse_simple``;
        on failure we keep the regex intact and show the complex-pattern
        notice with Simple inputs disabled.
        """
        simple_visible = self._mode == MODE_SIMPLE and self._match_fn is not None
        self._simple_widget.setVisible(simple_visible)
        self._regex_widget.setVisible(not simple_visible)

        if not simple_visible:
            return

        parsed = _try_parse_simple(self.regex.text())
        if parsed is None:
            # Regex too complex to represent in Simple — keep the regex
            # value verbatim, show the notice, disable Simple inputs so
            # the user can't accidentally clobber the regex by typing.
            self._simple_complex_notice.show()
            self._simple_op_combo.setEnabled(False)
            self._simple_text.setEnabled(False)
            return

        op_key, plain_text = parsed
        # Populate the Simple inputs with signals blocked so the
        # populate doesn't trigger a write-through that re-stamps
        # the regex (which would be a no-op but adds noise on the
        # text-changed signal chain).
        op_idx = self._simple_op_combo.findData(op_key)
        if op_idx >= 0:
            self._simple_op_combo.blockSignals(True)
            try:
                self._simple_op_combo.setCurrentIndex(op_idx)
            finally:
                self._simple_op_combo.blockSignals(False)
        self._simple_text.blockSignals(True)
        try:
            self._simple_text.setText(plain_text)
        finally:
            self._simple_text.blockSignals(False)
        self._simple_complex_notice.hide()
        self._simple_op_combo.setEnabled(True)
        self._simple_text.setEnabled(True)

    # ── Simple → regex write-through ───────────────────────────────────────

    def _writeto_regex_from_simple(self, *_args) -> None:
        """Synthesise a regex from the Simple inputs and stamp it onto
        ``self.regex`` so the regex line edit is always the canonical
        pattern. blockSignals around setText avoids a feedback loop with
        the validator + preview timer that already listen on regex
        changes — we re-fire the preview timer manually so it sees the
        new value with the right field context.
        """
        if self._mode != MODE_SIMPLE or self._match_fn is None:
            return
        text = self._simple_text.text()
        if not text:
            pattern = ""
        else:
            op_key = self._simple_op_combo.currentData() or "contains"
            pattern = ""
            for k, _label_key, builder in _SIMPLE_OPS:
                if k == op_key:
                    pattern = builder(text)
                    break
            if not pattern:
                pattern = re.escape(text)
        # Avoid a feedback loop: block signals while we replace the
        # canonical text, then refresh validation + preview manually.
        self.regex.blockSignals(True)
        try:
            self.regex.setText(pattern)
        finally:
            self.regex.blockSignals(False)
        self._validate_regex()
        if hasattr(self, "_preview_timer"):
            self._preview_timer.start()

    # ── Pattern build ──────────────────────────────────────────────────────

    def _build_pattern(self) -> str:
        """Return the canonical pattern.

        Simple mode writes through to ``self.regex`` on every change
        (see ``_writeto_regex_from_simple``), so the regex line edit is
        always the single source of truth — both modes read from it.
        """
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
        if self._mode == MODE_SIMPLE:
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
