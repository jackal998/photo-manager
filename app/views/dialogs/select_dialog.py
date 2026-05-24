"""Dialog for setting action on items matching a field/regex."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from loguru import logger

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
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
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from app.views.constants import settable_decisions
from app.views.window_state import (
    QSETTINGS_KEY_ACTION_DIALOG_GEOM,
    QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE,
    restore_splitter_state,
    restore_widget_geometry,
    save_splitter_state,
    save_widget_geometry,
    window_state_qsettings,
)
from infrastructure.i18n import t

# Maps the internal English field name (used as the lookup key in
# regex matching and column dispatch) to its column.* translation key.
# The dialog displays the translated label but emits the English name
# in setActionRequested so downstream regex matchers stay locale-free.
#
# C15 from #349 (Wave 6, won't-fix): this map deliberately lives here,
# not in app.views.constants where ``settable_decisions`` lives. The
# two paths cover different semantic domains: field labels are
# dialog-local column-display names used only by this combo, while
# decision labels are app-wide action choices also consumed by
# ``context_menu.py`` and ``execute_action_dialog.py``. Unifying
# them would push field-label logic into a constants module where
# it doesn't belong, or pull decision logic into this dialog where
# it can't be shared — both worse couplings than the current
# separation.
_FIELD_LABEL_KEYS: dict[str, str] = {
    "Similarity":    "column.similarity",
    "Action":        "column.action",
    "Score":         "column.score",          # #238 — added after #187 scoring rollout
    "Lock":          "column.lock",           # #238 — was emitting raw "Lock" before
    "File Name":     "column.file_name",
    "Folder":        "column.folder",
    "Size (Bytes)":  "column.size_bytes",
    "Group Count":   "column.group_count",
    "Creation Date": "column.creation_date",
    "Shot Date":     "column.shot_date",
    "Resolution":    "column.resolution",     # #238 — string field; regex matches "WxH"
}

# Type alias for the live-preview match function. Callers (DialogHandler
# from MainWindow, ExecuteActionDialog inline) build this via
# `app.views.handlers.file_operations.build_match_fn` and pass it in.
# Returns (matched_count, total_count, samples) where each sample is a
# (basename, matched_field_str) tuple. The preview displays
# matched_field_str so non-File-Name regexes show *why* each row
# matched (A2 from #347 — Wave 4). For the File Name field the two
# tuple elements are identical. Sample list is bounded by
# build_match_fn's sample_cap; matched count is the full total.
MatchFn = Callable[[str, str], tuple[int, int, list[tuple[str, str]]]]

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
# A8: mode key is now per-context (see context_id parameter). The
# legacy key "ui.action_dialog.mode" is read as a fallback so
# existing user state migrates seamlessly.
_MODE_KEY_TEMPLATE = "ui.action_dialog.{context_id}.mode"
_MODE_KEY_LEGACY = "ui.action_dialog.mode"
# A8: field and simple_op keys are also per-context (E3, E8).
_FIELD_KEY_TEMPLATE = "ui.action_dialog.{context_id}.field"
_SIMPLE_OP_KEY_TEMPLATE = "ui.action_dialog.{context_id}.simple_op"
_RECENT_CAP = 10

# Fields whose underlying record attribute is numeric (or a datetime
# that maps cleanly to a sortable timestamp). For these fields the
# dialog swaps the regex/simple panel for a numeric-condition panel
# (threshold comparison or Top-N within group). When the selected
# field is NOT in this set, the existing regex/simple controls show
# unchanged. #209.
_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "Size (Bytes)",
    "Group Count",
    "Similarity",
    "Score",
    "Creation Date",
    "Shot Date",
})

# Subset of _NUMERIC_FIELDS where the threshold input expects an ISO
# date (YYYY-MM-DD) rather than a bare number. Drives the field-aware
# placeholder in the numeric panel's value line edit. A5 from #347 —
# without this, Size users were told "or YYYY-MM-DD" and Date users
# were told "type a number".
_DATE_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "Creation Date",
    "Shot Date",
})

# Threshold-comparison operators. Order is the dropdown order shown
# to the user; the first item (">") is the default because it matches
# the most common intent ("delete rows below score X" → keep > X).
_CMP_OPS: list[tuple[str, str]] = [
    (">",  "action_dialog.cmp_op_gt"),
    (">=", "action_dialog.cmp_op_ge"),
    ("<",  "action_dialog.cmp_op_lt"),
    ("<=", "action_dialog.cmp_op_le"),
    ("==", "action_dialog.cmp_op_eq"),
    ("!=", "action_dialog.cmp_op_ne"),
]

# Internal mode flags for the numeric panel.
NUMERIC_MODE_THRESHOLD = "threshold"
NUMERIC_MODE_TOPN = "top_n"

# Pattern-string prefixes encoding numeric conditions through the
# existing setActionRequested(field, pattern, decision) signal. The
# receiver inspects the prefix and routes accordingly — keeps the
# dialog→handler contract a single string and avoids a new signal.
PATTERN_CMP_PREFIX = "__cmp__:"
PATTERN_TOP_N_PREFIX = "__top_n__:"


def _numeric_value_for(field: str, rec: Any, group: Any) -> float | None:
    """Return the comparable numeric value of ``field`` for ``rec``.

    For date fields the datetime is converted to a POSIX timestamp so
    threshold comparisons stay in floats. Returns ``None`` when the
    attribute is missing or unset — caller skips such records (same
    semantics as ``_get_record_field`` returning ``None``).
    """
    if field == "Size (Bytes)":
        val = getattr(rec, "file_size_bytes", None)
        return float(val) if val is not None else None
    if field == "Group Count":
        items = getattr(group, "items", None)
        return float(len(items)) if items is not None else None
    if field == "Similarity":
        val = getattr(rec, "hamming_distance", None)
        return float(val) if val is not None else None
    if field == "Score":
        val = getattr(rec, "score", None)
        return float(val) if val is not None else None
    if field == "Creation Date":
        d = getattr(rec, "creation_date", None)
        try:
            return d.timestamp() if d is not None else None
        except Exception:
            return None
    if field == "Shot Date":
        d = getattr(rec, "shot_date", None)
        try:
            return d.timestamp() if d is not None else None
        except Exception:
            return None
    return None


def _parse_threshold(field: str, text: str) -> float | None:
    """Parse the user's threshold text into a numeric value to compare against.

    Pure numeric fields accept a float. Date fields accept ISO ``YYYY-MM-DD``
    (or ``YYYY-MM-DD HH:MM:SS``) and we convert to a timestamp so the
    threshold matches ``_numeric_value_for``'s float-of-timestamp form.
    Returns ``None`` for unparseable input — the caller treats this as
    a zero-match condition (same as an invalid regex in the existing flow).
    """
    text = (text or "").strip()
    if not text:
        return None
    if field in ("Creation Date", "Shot Date"):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
        # Fall through to bare-float — lets power users paste a
        # timestamp if they want, but the common case is ISO.
    try:
        return float(text)
    except ValueError:
        return None


def _cmp_apply(value: float, op: str, threshold: float) -> bool:
    """Evaluate ``value <op> threshold``. Unknown op → False (defensive)."""
    if op == ">":  return value > threshold
    if op == ">=": return value >= threshold
    if op == "<":  return value < threshold
    if op == "<=": return value <= threshold
    if op == "==": return value == threshold
    if op == "!=": return value != threshold
    return False


def select_paths_by_threshold(
    groups: list, field: str, op: str, threshold_text: str
) -> list[str]:
    """Return file_paths from ``groups`` whose ``field`` value passes ``op threshold``.

    Records whose numeric value is missing (None) are skipped — same
    rule as the regex flow, which skips fields ``_get_record_field``
    returns None for. Order is group-then-record so the caller's
    truncated lock-confirm list reads as user tree order.
    """
    threshold = _parse_threshold(field, threshold_text)
    if threshold is None:
        return []
    matched: list[str] = []
    for group in groups:
        for rec in getattr(group, "items", []):
            val = _numeric_value_for(field, rec, group)
            if val is None:
                continue
            if _cmp_apply(val, op, threshold):
                matched.append(rec.file_path)
    return matched


def select_paths_top_n(
    groups: list, field: str, n: int, order: str
) -> list[str]:
    """Return file_paths ranked top (or bottom) ``n`` within each group.

    ``order='desc'`` selects the N records with the LARGEST values —
    "top by score" picks the keepers. ``order='asc'`` selects the N
    records with the SMALLEST values — "bottom by score" picks the
    deletables. Ties break by file_path so the selection is stable
    across re-runs (don't want a coin-flip on which of two equal-score
    siblings gets selected). Records with no numeric value (None) are
    excluded from ranking entirely.

    When a group has fewer than N rankable records, all of its rankable
    records are selected — Top 3 of a 2-row group selects both rows.
    """
    if n <= 0 or order not in ("asc", "desc"):
        return []
    matched: list[str] = []
    reverse = (order == "desc")
    for group in groups:
        ranked: list[tuple[float, str]] = []
        for rec in getattr(group, "items", []):
            val = _numeric_value_for(field, rec, group)
            if val is None:
                continue
            ranked.append((val, rec.file_path))
        # Stable sort by (value, file_path). For desc, sort by
        # (-value, file_path) so the tiebreaker stays ascending —
        # picking the alphabetically-earlier path among equals is
        # arbitrary but deterministic.
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=False)
        if reverse:
            ranked.reverse()
            # After reverse the tiebreaker reads desc(path); flip
            # tiebreaker back to asc(path) within each value bucket.
            # Cheapest correct way: group by value and re-sort each
            # group's paths ascending. With small N (typical use:
            # n=1..5) the dataset per group is small.
            from itertools import groupby
            fixed: list[tuple[float, str]] = []
            for _val, grp in groupby(ranked, key=lambda t: t[0]):
                fixed.extend(sorted(grp, key=lambda t: t[1]))
            ranked = fixed
        for _val, path in ranked[:n]:
            matched.append(path)
    return matched


def _select_top_n_with_metadata(
    groups: list, field: str, n: int, order: str
) -> list[tuple[int, str, float]]:
    """Like :func:`select_paths_top_n` but also returns each pick's
    group identifier (1-based) and the value the ranking selected on.

    Used by :meth:`ActionDialog._refresh_numeric_preview` to label
    preview rows with their group + value (D5 + D8 from #350, Wave 4).
    The public :func:`select_paths_top_n` keeps its ``list[str]``
    shape because :meth:`ExecuteActionDialog._matched_paths_for_pattern`
    only needs paths to encode the ``__top_n__:`` pseudo-pattern.
    """
    if n <= 0 or order not in ("asc", "desc"):
        return []
    out: list[tuple[int, str, float]] = []
    reverse = (order == "desc")
    for group_idx, group in enumerate(groups, start=1):
        group_no = getattr(group, "group_number", None) or group_idx
        ranked: list[tuple[float, str]] = []
        for rec in getattr(group, "items", []):
            val = _numeric_value_for(field, rec, group)
            if val is None:
                continue
            ranked.append((val, rec.file_path))
        ranked.sort(key=lambda t: (t[0], t[1]), reverse=False)
        if reverse:
            ranked.reverse()
            from itertools import groupby
            fixed: list[tuple[float, str]] = []
            for _val, grp in groupby(ranked, key=lambda t: t[0]):
                fixed.extend(sorted(grp, key=lambda t: t[1]))
            ranked = fixed
        for val, path in ranked[:n]:
            out.append((group_no, path, val))
    return out


def encode_cmp_pattern(op: str, value_text: str) -> str:
    """Encode a threshold condition as a single pattern string for transit
    through ``setActionRequested(field, pattern, decision)``.

    Format: ``__cmp__:<op>:<value_text>``. Value text is the user's raw
    input so the receiver can re-parse it with the field's own rules
    (numeric vs. ISO date). Value is the last segment, so user input
    containing ``:`` (e.g. ``2026-01-01 12:00:00``) still round-trips.
    """
    return f"{PATTERN_CMP_PREFIX}{op}:{value_text}"


def encode_top_n_pattern(n: int, order: str) -> str:
    """Encode a Top-N condition as a pattern string. Format:
    ``__top_n__:<n>:<order>`` where order ∈ {asc, desc}.
    """
    return f"{PATTERN_TOP_N_PREFIX}{n}:{order}"


def decode_cmp_pattern(pattern: str) -> tuple[str, str] | None:
    """Reverse of :func:`encode_cmp_pattern`. Returns (op, value_text)
    or ``None`` if the pattern isn't a cmp-encoded string."""
    if not pattern.startswith(PATTERN_CMP_PREFIX):
        return None
    rest = pattern[len(PATTERN_CMP_PREFIX):]
    # op is first segment, value is everything after — value may
    # contain ``:`` (ISO timestamp with seconds).
    if ":" not in rest:
        return None
    op, value_text = rest.split(":", 1)
    return op, value_text


def decode_top_n_pattern(pattern: str) -> tuple[int, str] | None:
    """Reverse of :func:`encode_top_n_pattern`. Returns (n, order) or
    ``None`` if pattern isn't a top-n-encoded string."""
    if not pattern.startswith(PATTERN_TOP_N_PREFIX):
        return None
    rest = pattern[len(PATTERN_TOP_N_PREFIX):]
    parts = rest.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        n = int(parts[0])
    except ValueError:
        return None
    order = parts[1]
    if order not in ("asc", "desc"):
        return None
    return n, order


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
      * Simple / Regex mode toggle. Simple replaces the regex line
        edit with "Find rows where it [contains | starts with | ends
        with | exactly matches] [text]" and builds the regex internally
        so non-regex users never type a ``\\d`` in their lives.
        (Phase B shipped this as "Beginner" mode; renamed to "Simple"
        in Phase C for tone — see ``_LEGACY_MODE_VALUES``.)
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
        groups: list | None = None,
        context_id: str = "main",
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
        # ``groups`` is the raw list of PhotoGroups whose rows the
        # dialog would affect — passed in by ExecuteActionDialog so
        # the new numeric-condition panel can rank records for Top-N
        # within group (#209). When None, the numeric panel never
        # appears: callers that don't supply groups (main-window
        # standalone) keep the existing regex/simple-only behavior.
        self._groups = groups if groups is not None else []
        self._numeric_mode = NUMERIC_MODE_THRESHOLD
        # B9 from #348 (Wave 9b-trim): remember the most recent matched
        # count from the last preview refresh so _emit_set_action can flash
        # "Applied to N rows" in the match counter. None when no preview
        # has run yet (i.e. match_fn is None — flat layout never tracks).
        self._last_matched_count: int | None = None
        # C13 from #349 (Wave 8): the splitter exists only when match_fn
        # is supplied. Promote it to self._splitter so `done()` can save
        # its state — pre-Wave-8 it was a local variable and the handle
        # position was lost on every close. ``None`` for the flat-layout
        # branch which has no splitter at all (E4 invariant).
        self._splitter: QSplitter | None = None

        # A8: per-context settings keys so "main" and "execute" entry
        # points persist independent mode/field/op preferences.
        self._context_id = context_id
        self._mode_key = _MODE_KEY_TEMPLATE.format(context_id=context_id)
        self._field_key = _FIELD_KEY_TEMPLATE.format(context_id=context_id)
        self._simple_op_key = _SIMPLE_OP_KEY_TEMPLATE.format(context_id=context_id)

        # C1+C4: mode toggle is always created (Simple disabled when
        # match_fn is None). Default logic: no match_fn → Regex only;
        # with match_fn → read per-context key, fall back to legacy
        # global key, then default to Simple.
        if self._match_fn is None:
            self._mode = MODE_REGEX
        else:
            # A8: per-context key first, legacy global key as fallback.
            persisted = self._settings_get(self._mode_key, None)
            if persisted is None:
                persisted = self._settings_get(_MODE_KEY_LEGACY, MODE_SIMPLE)
            self._mode = _LEGACY_MODE_VALUES.get(persisted, MODE_SIMPLE)

        # A6 + E2-upgrade: _recent_patterns now stores (field, pattern)
        # tuples. The shape-validator accepts: valid tuples, legacy bare
        # strings (migrated to (None, str) with a warning), and drops
        # anything malformed with a warning.
        _raw_recent = self._settings_get(_RECENT_KEY, []) or []
        if not isinstance(_raw_recent, list):
            _raw_recent = []
        _cleaned: list[tuple[str | None, str]] = []
        _changed = False
        for entry in _raw_recent:
            if (
                isinstance(entry, tuple)
                and len(entry) == 2
                and isinstance(entry[0], str)
                and isinstance(entry[1], str)
                and entry[0]
                and entry[1]
            ):
                _cleaned.append(entry)
            elif isinstance(entry, str) and entry:
                # Legacy bare-string: migrate to (None, pattern).
                # None field means "applies to any field" — shown in
                # Recent menu only when field-gating allows it.
                logger.warning(
                    "ActionDialog: migrating legacy recent pattern {!r} to tuple",
                    entry,
                )
                _cleaned.append((None, entry))
                _changed = True
            else:
                logger.warning(
                    "ActionDialog: dropping malformed recent entry {!r}", entry
                )
                _changed = True
        self._recent_patterns: list[tuple[str | None, str]] = _cleaned
        if _changed:
            self._settings_set(_RECENT_KEY, self._recent_patterns)

        # Build the per-field combo box, regex line edit, action combo, and
        # buttons. They live in this `left_layout` regardless of whether
        # the preview pane is constructed — the preview pane just sits
        # next to them when present.
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # ── Mode toggle (C1: always created; Simple disabled when no match_fn)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel(t("action_dialog.mode_label")))
        self._mode_simple_btn = QRadioButton(t("action_dialog.mode_simple"))
        self._mode_simple_btn.setObjectName("regexModeSimple")
        self._mode_regex_btn = QRadioButton(t("action_dialog.mode_regex"))
        self._mode_regex_btn.setObjectName("regexModeRegex")
        mode_row.addWidget(self._mode_simple_btn)
        mode_row.addWidget(self._mode_regex_btn)
        # C2: Recent button lives in the mode row (always visible, not
        # buried inside _regex_widget where it disappeared in Simple mode).
        self._recent_btn = QPushButton(t("action_dialog.recent_button"))
        self._recent_btn.setObjectName("regexRecentButton")
        # C16 from #349 (Wave 8): use QStyle's standard down-arrow icon
        # instead of the Unicode "▾" character. Unicode rendering varies
        # by font/platform (sometimes shows the literal "Recent ▾" with a
        # tofu glyph); the standard icon follows the system theme.
        self._recent_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self._recent_btn.clicked.connect(self._show_recent_menu)
        mode_row.addWidget(self._recent_btn)
        mode_row.addStretch(1)
        left_layout.addLayout(mode_row)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._mode_simple_btn)
        mode_group.addButton(self._mode_regex_btn)
        self._mode_button_group = mode_group  # keep ref alive
        if self._match_fn is None:
            # C1: Simple is meaningless without a live-preview data source —
            # disable it with a descriptive tooltip.
            self._mode_simple_btn.setEnabled(False)
            self._mode_simple_btn.setToolTip(
                t("action_dialog.simple_disabled_no_match_fn")
            )
            self._mode_regex_btn.setChecked(True)
        else:
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
        # D2 from #350 (Wave 9a): native "×" clear button — one-click input wipe.
        self._simple_text.setClearButtonEnabled(True)
        self._simple_text.setPlaceholderText(t("action_dialog.simple_text_placeholder"))
        simple_inputs_row.addWidget(self._simple_text, stretch=1)
        simple_outer.addLayout(simple_inputs_row)
        self._simple_complex_notice = QLabel(t("action_dialog.simple_complex_notice"))
        self._simple_complex_notice.setObjectName("regexSimpleComplexNotice")
        # C9 from #349 (Wave 8): hex amber stylesheet removed — broke dark
        # mode. Bold weight conveys emphasis; the notice text itself ("switch
        # to Regex") supplies the warning semantics. Qt has no canonical
        # "warning" palette role, so system text color is the right neutral.
        _notice_font = self._simple_complex_notice.font()
        _notice_font.setBold(True)
        self._simple_complex_notice.setFont(_notice_font)
        self._simple_complex_notice.setWordWrap(True)
        self._simple_complex_notice.hide()
        simple_outer.addWidget(self._simple_complex_notice)
        # B2+B4: "Switch to Regex" button shown alongside the complex-pattern
        # notice. Created unconditionally at __init__ time (not on notice-show)
        # to avoid GC issues. Hidden by default; shown/hidden with the notice.
        # On click, triggers _mode_regex_btn (the regex line edit already holds
        # the complex pattern — lossless switch).
        self._switch_to_regex_btn = QPushButton(t("action_dialog.switch_to_regex"))
        self._switch_to_regex_btn.setObjectName("regexSwitchToRegexBtn")
        self._switch_to_regex_btn.clicked.connect(
            lambda: self._mode_regex_btn.setChecked(True)
        )
        self._switch_to_regex_btn.hide()
        simple_outer.addWidget(self._switch_to_regex_btn)
        left_layout.addWidget(self._simple_widget)

        # ── Regex-mode container (regex line edit + validation +
        #    cheatsheet chips) ─────────────────────────────────────────────
        # C3: cheatsheet stays inside _regex_widget (Regex-specific affordance
        # only meaningful when actively typing a regex; moving it to an
        # always-visible row would clutter Simple-mode users who never need it).
        self._regex_widget = QWidget()
        self._regex_widget.setObjectName("regexRegexRow")
        regex_layout = QVBoxLayout(self._regex_widget)
        regex_layout.setContentsMargins(0, 0, 0, 0)

        regex_row = QHBoxLayout()
        regex_row.addWidget(QLabel(t("action_dialog.regex_label")))
        self.regex = QLineEdit()
        self.regex.setObjectName("regexLineEdit")
        # D2 from #350 (Wave 9a): native "×" clear button — one-click input wipe.
        self.regex.setClearButtonEnabled(True)
        self.regex.setPlaceholderText(t("action_dialog.regex_placeholder"))
        regex_row.addWidget(self.regex)

        self._validation_icon = QLabel("")
        self._validation_icon.setObjectName("regexValidationIcon")
        self._validation_icon.setFixedWidth(16)
        regex_row.addWidget(self._validation_icon)
        regex_layout.addLayout(regex_row)

        # Friendly error string sits directly under the regex row, hidden
        # when the regex compiles. Coloring the label red is enough; we
        # don't restyle the QLineEdit border (focus-ring fights with the
        # native Windows style on PySide6).
        self._validation_error = QLabel("")
        self._validation_error.setObjectName("regexValidationError")
        # C8 from #349 (Wave 8): hex red stylesheet removed — broke dark
        # mode. Bold weight gives emphasis; the prefix "Invalid regex:"
        # supplies the error semantics. Qt has no "error" palette role.
        _err_font = self._validation_error.font()
        _err_font.setBold(True)
        self._validation_error.setFont(_err_font)
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

        # ── Numeric-condition container (only built when groups passed) ────
        # The numeric panel covers two condition types: threshold
        # comparison (>=, <=, == …) against a value the user types,
        # and Top/Bottom N within group. Only shown when the active
        # field is in _NUMERIC_FIELDS AND groups were supplied (#209).
        # Stays hidden by default — _apply_field_panel_visibility flips
        # it on at the moment the user picks a numeric field.
        self._numeric_widget = QWidget()
        self._numeric_widget.setObjectName("numericConditionRow")
        numeric_outer = QVBoxLayout(self._numeric_widget)
        numeric_outer.setContentsMargins(0, 0, 0, 0)

        # Mode toggle: Threshold | Top N per group
        num_mode_row = QHBoxLayout()
        self._num_mode_threshold_btn = QRadioButton(
            t("action_dialog.numeric_mode_threshold")
        )
        self._num_mode_threshold_btn.setObjectName("numericModeThreshold")
        self._num_mode_topn_btn = QRadioButton(
            t("action_dialog.numeric_mode_topn")
        )
        self._num_mode_topn_btn.setObjectName("numericModeTopN")
        self._num_mode_threshold_btn.setChecked(True)
        num_mode_row.addWidget(self._num_mode_threshold_btn)
        num_mode_row.addWidget(self._num_mode_topn_btn)
        num_mode_row.addStretch(1)
        numeric_outer.addLayout(num_mode_row)
        num_mode_group = QButtonGroup(self)
        num_mode_group.addButton(self._num_mode_threshold_btn)
        num_mode_group.addButton(self._num_mode_topn_btn)
        self._num_mode_button_group = num_mode_group  # retain ref

        # Threshold sub-panel: op combo + value line edit + validation
        # icon + (collapsible) error label. A4 from #347 (Wave 5) adds
        # the icon and error so unparseable threshold input is surfaced
        # — pre-Wave-5 bad input silently produced 0 matches with no
        # signal that the *threshold*, not the data, was the problem.
        self._num_threshold_widget = QWidget()
        threshold_outer = QVBoxLayout(self._num_threshold_widget)
        threshold_outer.setContentsMargins(0, 0, 0, 0)
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel(t("action_dialog.numeric_threshold_label")))
        self._num_cmp_combo = QComboBox()
        self._num_cmp_combo.setObjectName("numericCmpCombo")
        for op_key, label_key in _CMP_OPS:
            self._num_cmp_combo.addItem(t(label_key), userData=op_key)
        threshold_row.addWidget(self._num_cmp_combo)
        self._num_value_edit = QLineEdit()
        self._num_value_edit.setObjectName("numericValueEdit")
        # Placeholder is field-aware (A5 from #347) — set via
        # _update_numeric_value_placeholder so date and number fields
        # get their own hint text rather than one combined string.
        threshold_row.addWidget(self._num_value_edit, stretch=1)
        self._num_threshold_icon = QLabel("")
        self._num_threshold_icon.setObjectName("numericThresholdIcon")
        self._num_threshold_icon.setFixedWidth(16)
        threshold_row.addWidget(self._num_threshold_icon)
        threshold_outer.addLayout(threshold_row)
        self._num_threshold_error = QLabel("")
        self._num_threshold_error.setObjectName("numericThresholdError")
        # C8 from #349 (Wave 8): hex red removed (see _validation_error
        # for rationale). Bold + system text color.
        _num_err_font = self._num_threshold_error.font()
        _num_err_font.setBold(True)
        self._num_threshold_error.setFont(_num_err_font)
        self._num_threshold_error.setWordWrap(True)
        self._num_threshold_error.hide()
        threshold_outer.addWidget(self._num_threshold_error)
        numeric_outer.addWidget(self._num_threshold_widget)

        # Top-N sub-panel: order combo (Top/Bottom) + N spinbox.
        self._num_topn_widget = QWidget()
        topn_row = QHBoxLayout(self._num_topn_widget)
        topn_row.setContentsMargins(0, 0, 0, 0)
        topn_row.addWidget(QLabel(t("action_dialog.numeric_topn_label")))
        self._num_order_combo = QComboBox()
        self._num_order_combo.setObjectName("numericOrderCombo")
        # "desc" first because top-by-score (highest-first) is the most
        # common ranking intent — keepers at the top.
        self._num_order_combo.addItem(t("action_dialog.numeric_order_top"), userData="desc")
        self._num_order_combo.addItem(t("action_dialog.numeric_order_bottom"), userData="asc")
        topn_row.addWidget(self._num_order_combo)
        self._num_n_spin = QSpinBox()
        self._num_n_spin.setObjectName("numericNSpinBox")
        # A14 from #347 (Wave 5): cap was 999, raised to 10_000 so
        # large-group manifests can "keep everything" via Top-N when
        # a group has >999 records. Qt requires an upper bound on
        # QSpinBox, so this is the new practical ceiling.
        self._num_n_spin.setRange(1, 10_000)
        self._num_n_spin.setValue(1)
        topn_row.addWidget(self._num_n_spin)
        topn_row.addWidget(QLabel(t("action_dialog.numeric_topn_suffix")))
        topn_row.addStretch(1)
        numeric_outer.addWidget(self._num_topn_widget)
        self._num_topn_widget.hide()  # threshold is the default sub-mode

        # Connect the numeric-mode radios so toggling shows/hides the
        # right sub-panel and re-runs the live preview. E7 from #351
        # (Wave 5): wired via QButtonGroup.buttonToggled rather than
        # the single-radio .toggled signal. The group fires once per
        # button per flip with (button, checked) so the handler can
        # identify which radio went active — robust against a third
        # radio being added later.
        self._num_mode_button_group.buttonToggled.connect(
            self._on_numeric_mode_toggled
        )

        self._numeric_widget.hide()  # initial state: hidden until field changes
        left_layout.addWidget(self._numeric_widget)

        # ── Match counter row (visible in BOTH modes when match_fn) ────────
        # Lives outside the mode containers so toggling mode never hides
        # the live count — it's the primary feedback for both Simple
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
        # include_lock=True adds "lock" / "unlock" so users can bulk-pin
        # decisions before a broader regex sweep, and bulk-unlock at
        # execute time as the escape hatch. See photo-manager#164.
        # B13 from #348 (Wave 6, won't-fix): _action_combo is populated
        # once here and never re-localized. That's safe because locale
        # change is wired through MenuController._on_language_chosen →
        # MainWindow.relocalize() which tears down and rebuilds the
        # entire MainWindow — including any open ActionDialog, which
        # is modal and therefore cannot be open during a locale
        # switch in the first place. Repopulation is structurally
        # impossible while the dialog is alive; no signal subscribe
        # is needed.
        self._decisions = settable_decisions(include_remove=True, include_lock=True)
        for label, _value in self._decisions:
            self._action_combo.addItem(label)
        action_row.addWidget(self._action_combo)
        self._btn_set_action = QPushButton(t("action_dialog.apply_button"))
        self._btn_set_action.setObjectName("regexApplyButton")
        action_row.addWidget(self._btn_set_action)
        action_row.addStretch(1)
        left_layout.addLayout(action_row)

        # E5 from #351 (Wave 8): "Reset window size" wipes the persisted
        # geometry + splitter blobs so the dialog reopens at the hardcoded
        # defaults (720×380, [420, 380]). Only meaningful when the splitter
        # exists — wired up below in the match_fn branch. Created
        # unconditionally + parented to ``self`` so test fixtures can find
        # it by objectName even on the flat-layout branch where it never
        # gets added to a visible layout. #391 moved it from the close-row
        # to the preview-header (added inside _build_preview_pane).
        self.btn_reset_geometry = QPushButton(
            t("action_dialog.reset_window_size_button"), self
        )
        self.btn_reset_geometry.setObjectName("regexResetGeometryButton")
        self.btn_reset_geometry.setToolTip(
            t("action_dialog.reset_window_size_tooltip")
        )
        # #391: Close button removed. OS title-bar X and Esc-key already
        # cover dismissal — Qt's default key event handling on a QDialog
        # maps Esc to reject(), and the title-bar X fires the same path.
        # No close_row exists anymore; the bottom of the left pane is the
        # Apply / mode-toggle action row.

        # ── Compose root layout ────────────────────────────────────────────
        # Two shapes: with preview (QSplitter holding left + right panes)
        # and without (flat layout — left widget is the whole dialog body).
        # Tests and QA scenarios that don't pass match_fn see the original
        # shape, so their UIA paths and findChild lookups stay valid.
        root = QVBoxLayout(self)
        if self._match_fn is not None:
            self._splitter = QSplitter(Qt.Orientation.Horizontal)
            self._splitter.addWidget(left_widget)
            self._splitter.addWidget(self._build_preview_pane())
            self._splitter.setSizes([420, 380])
            # D1 from #350 (Wave 8): default Qt splitter handle is ~1-5 px
            # wide on Windows — easy to miss. 8 px gives a comfortable
            # grab target without dominating the layout.
            self._splitter.setHandleWidth(8)
            root.addWidget(self._splitter)
            # Shrunk after dropping the wrapped tips paragraph and
            # restructuring chips into compact button-with-aside-label
            # rows. The dialog used to feel "too tall" — particularly
            # in Simple mode where the regex/chip section is hidden.
            # #215 — previously hardcoded ``self.resize(780, 420)``;
            # now the minimum is the only hardcoded default and the
            # user's last manual resize is restored on top of it.
            # ``restore_widget_geometry``'s off-screen guard falls
            # back to ``setMinimumSize`` defaults when the saved
            # rect would land on a disconnected monitor.
            self.setMinimumSize(720, 380)
            restore_widget_geometry(self, QSETTINGS_KEY_ACTION_DIALOG_GEOM)
            # C13: restore the saved splitter handle position on top of the
            # default [420, 380]. ``restore_splitter_state`` is a no-op when
            # no blob exists, so first-time users keep the default sizes.
            restore_splitter_state(
                self._splitter, QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE
            )
        else:
            # E4 from #351 (Wave 8): the flat-layout branch is intentionally
            # geometry-free — there is no splitter and no user-resizable
            # frame to persist. The audit item ("restore on both branches")
            # is scoped here by C13 making the splitter branch fully
            # symmetric; the flat branch needs no save and no restore.
            root.addWidget(left_widget)
            left_layout.setContentsMargins(11, 11, 11, 11)

        self._btn_set_action.clicked.connect(self._emit_set_action)
        # E5: reset only makes sense when the splitter exists (there's
        # nothing else resizable on the flat-layout branch). On flat
        # layout the button is parented to ``self`` (for test fixture
        # lookup) but explicitly hidden so the contract pinned by
        # ``TestResetGeometry.test_reset_button_hidden_without_match_fn``
        # holds. Ctrl+0 shortcut is also splitter-only.
        if self._splitter is not None:
            self.btn_reset_geometry.clicked.connect(self._reset_geometry)
            self._reset_geometry_shortcut = QShortcut(
                QKeySequence("Ctrl+0"), self
            )
            self._reset_geometry_shortcut.activated.connect(
                self._reset_geometry
            )
        else:
            self.btn_reset_geometry.hide()

        # D9 from #350 (Wave 9a): Ctrl+Enter is the power-user shortcut
        # for Apply. Unconditional (no setDefault() on the button, so Enter
        # alone does NOT trigger Apply — only Ctrl+Enter does). Works in
        # both splitter and flat layouts because Apply is universal.
        self._apply_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._apply_shortcut.activated.connect(self._emit_set_action)

        if initial_field and self.combo.findData(initial_field) >= 0:
            # E8: initial_field from caller (column-click) takes precedence
            # over any persisted field preference.
            self._set_default_field(initial_field)
        else:
            # E8: restore persisted field only when initial_field is None
            # (no column context from the caller).
            saved_field = self._settings_get(self._field_key, None)
            if saved_field and self.combo.findData(saved_field) >= 0:
                self._set_default_field(saved_field)
            else:
                self._set_default_field("File Name")
        # A1 from #347: track the prior field so _on_field_changed can
        # tell whether the user typed something custom (preserve it)
        # or the regex is still the auto-default from the prior field
        # (safe to overwrite with the new field's default).
        self._previous_field = self._current_field()
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
            # #209 — numeric panel inputs feed the same debounced
            # preview so the match counter updates as the user types
            # a threshold or scrolls the Top-N spinbox.
            self._num_value_edit.textChanged.connect(self._preview_timer.start)
            # A4 from #347 (Wave 5): threshold validation runs
            # synchronously (not debounced) on every keystroke so the
            # ✓/✗ icon tracks input without lag.
            self._num_value_edit.textChanged.connect(self._validate_threshold)
            self._num_cmp_combo.currentIndexChanged.connect(self._preview_timer.start)
            self._num_order_combo.currentIndexChanged.connect(self._preview_timer.start)
            self._num_n_spin.valueChanged.connect(self._preview_timer.start)
            self._num_mode_threshold_btn.toggled.connect(self._preview_timer.start)

        self._apply_exact_regex_for_current_field()
        # Apply the mode visibility AFTER all widgets exist.
        self._apply_mode_visibility()
        # E3: restore persisted simple_op AFTER _apply_mode_visibility so the
        # visibility call's reverse-parse doesn't overwrite the user's preferred
        # op. findData returns -1 for a stale/unknown key — leave combo at
        # index 0 ("contains") rather than calling setCurrentIndex(-1), which
        # would leave it blank. Only apply when Simple mode is actually showing
        # (when the regex is empty the reverse-parse reads ("contains", ""),
        # so the restore is the authoritative override).
        _saved_op = self._settings_get(self._simple_op_key, None)
        if _saved_op is not None and self._mode == MODE_SIMPLE:
            _op_idx = self._simple_op_combo.findData(_saved_op)
            if _op_idx >= 0:
                self._simple_op_combo.blockSignals(True)
                try:
                    self._simple_op_combo.setCurrentIndex(_op_idx)
                finally:
                    self._simple_op_combo.blockSignals(False)
                # Sync regex after op change (signals were blocked).
                self._writeto_regex_from_simple()
            # If findData returned -1 (stale key), leave combo at index 0.
        self._update_numeric_value_placeholder()
        self._validate_regex()
        if self._match_fn is not None:
            self._refresh_preview()
        self._focus_default_input()

    def _focus_default_input(self) -> None:
        """B14 from #350 (Wave 9a): land focus on the input the user is most
        likely to type into, picked per current mode/panel state.

        Pre-Wave-9a Qt's default first-focusable-widget behavior put focus
        on the field combo — the user always had to Tab or click before
        typing. After Wave 9a, opening the dialog drops the user straight
        into the relevant text input.

        Numeric panel takes precedence over mode because picking a numeric
        field swaps out the regex/Simple panels entirely.
        """
        if self._field_panel_is_numeric():
            if self._numeric_mode == NUMERIC_MODE_TOPN:
                self._num_n_spin.setFocus()
            else:
                self._num_value_edit.setFocus()
        elif self._mode == MODE_SIMPLE:
            self._simple_text.setFocus()
        else:
            self.regex.setFocus()

    # ── Settings helpers ───────────────────────────────────────────────────

    def _settings_get(self, key: str, default):
        # E1 from #351 (Wave 6): preserve the no-throw guarantee but
        # log on failure so a corrupt settings file leaves a
        # breadcrumb. Pre-Wave-6 the bare-except meant a malformed
        # ``ui.action_dialog.recent_patterns`` value (e.g. a string
        # where a list was expected) failed silently and the user's
        # Recent dropdown stayed empty with no signal.
        if self._settings is None:
            return default
        try:
            return self._settings.get(key, default)
        except Exception as exc:
            logger.warning(
                "ActionDialog: settings.get({!r}) failed: {}", key, exc
            )
            return default

    def _settings_set(self, key: str, value) -> None:
        # E9 from #351 (Wave 6): mirror E1 on the write path. A
        # read-only settings file or full disk used to silently fail
        # — user believed their patterns persisted; next launch
        # showed an empty Recent dropdown.
        if self._settings is None:
            return
        try:
            self._settings.set(key, value)
            self._settings.save()
        except Exception as exc:
            logger.warning(
                "ActionDialog: settings.set({!r}) failed: {}", key, exc
            )

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
        # A8: persist under per-context key.
        self._settings_set(self._mode_key, self._mode)
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

        #209: when the active field is numeric AND groups were provided,
        the numeric panel pre-empts both Simple and Regex panels.
        ``_field_panel_is_numeric`` is the gate.
        """
        if self._field_panel_is_numeric():
            self._simple_widget.setVisible(False)
            self._regex_widget.setVisible(False)
            self._numeric_widget.setVisible(True)
            self._apply_numeric_sub_visibility()
            return
        self._numeric_widget.setVisible(False)
        simple_visible = self._mode == MODE_SIMPLE and self._match_fn is not None
        self._simple_widget.setVisible(simple_visible)
        self._regex_widget.setVisible(not simple_visible)

        if not simple_visible:
            return

        parsed = _try_parse_simple(self.regex.text())
        if parsed is None:
            # Regex too complex to represent in Simple — keep the regex
            # value verbatim, show the notice + Switch-to-Regex button,
            # disable Simple inputs so the user can't accidentally clobber
            # the regex by typing.
            self._simple_complex_notice.show()
            self._switch_to_regex_btn.show()
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
        self._switch_to_regex_btn.hide()
        self._simple_op_combo.setEnabled(True)
        self._simple_text.setEnabled(True)

    # ── Numeric panel ──────────────────────────────────────────────────────

    def _field_panel_is_numeric(self) -> bool:
        """True iff the numeric-condition panel should pre-empt the
        regex/simple panels. Gated on (a) selected field is numeric-capable
        and (b) groups were supplied — without groups, Top-N can't rank
        and threshold comparisons have no rows to apply to. Every
        production callsite (main-window menu + right-click via
        ``dialog_handler``, Execute Action dialog) now passes ``groups=``
        explicitly; the gate only fires for unit-test callers that
        construct the dialog with groups=None (#237)."""
        if not self._groups:
            return False
        return self._current_field() in _NUMERIC_FIELDS

    def _apply_numeric_sub_visibility(self) -> None:
        """Show one sub-panel (threshold or top-n) inside the numeric widget."""
        is_threshold = self._numeric_mode == NUMERIC_MODE_THRESHOLD
        self._num_threshold_widget.setVisible(is_threshold)
        self._num_topn_widget.setVisible(not is_threshold)

    def _set_status_icon(self, label: QLabel, status: str | None) -> None:
        """Render a theme-aware status icon on ``label`` (C8 from #349, Wave 8).

        ``status`` is ``"valid"`` / ``"invalid"`` / ``None``. ``None`` clears
        the label (no pixmap, no text). The pixmaps come from the active
        Qt style's standard-icon set so they follow the system theme
        (Fusion light/dark, Windows native, etc.) instead of being pinned
        to the previous hex stylesheets that broke under dark mode.
        """
        if status is None:
            label.clear()
            return
        sp = (
            QStyle.StandardPixmap.SP_DialogApplyButton
            if status == "valid"
            else QStyle.StandardPixmap.SP_MessageBoxCritical
        )
        label.setPixmap(self.style().standardIcon(sp).pixmap(16, 16))

    def _validate_threshold(self) -> None:
        """Update threshold ✓/✗ icon + error label. A4 from #347 (Wave 5).

        Empty input is neutral (no icon, no error) — the user hasn't
        committed to a value yet. Non-empty but unparseable produces a
        ✗ + error string echoing the bad input so the user can see
        what didn't parse. Parseable input shows ✓.

        Re-runs on every keystroke (wired synchronously to
        ``_num_value_edit.textChanged`` so the icon tracks input
        without lag) AND from ``_on_field_changed`` because the same
        text can parse differently for Date vs Number fields.
        """
        if not self._field_panel_is_numeric():
            return
        text = self._num_value_edit.text().strip()
        if not text:
            self._set_status_icon(self._num_threshold_icon, None)
            self._num_threshold_icon.setAccessibleName("")
            self._num_threshold_icon.setToolTip("")
            self._num_threshold_error.hide()
            return
        field = self._current_field()
        parsed = _parse_threshold(field, text)
        if parsed is None:
            self._set_status_icon(self._num_threshold_icon, "invalid")
            # B11 from #348 (Wave 9a): mirror accessibleName → toolTip
            # for sighted hover users. Unlike _validate_regex (Wave 8 B3
            # hides icon on invalid), the threshold icon stays visible
            # on BOTH valid and invalid because its row layout has no
            # separate error label below the icon.
            tooltip_text = f"Threshold invalid: {text}"
            self._num_threshold_icon.setAccessibleName(tooltip_text)
            self._num_threshold_icon.setToolTip(tooltip_text)
            self._num_threshold_error.setText(
                t("action_dialog.invalid_threshold").format(input=text)
            )
            self._num_threshold_error.show()
        else:
            self._set_status_icon(self._num_threshold_icon, "valid")
            self._num_threshold_icon.setAccessibleName("Threshold valid")
            self._num_threshold_icon.setToolTip("Threshold valid")
            self._num_threshold_error.hide()

    def _update_numeric_value_placeholder(self) -> None:
        """Set the threshold-value placeholder based on the current field.

        A5 from #347: date fields (Creation Date, Shot Date) need a
        date-format hint; pure numeric fields need a number hint. A
        single combined placeholder ("type a number, or YYYY-MM-DD for
        dates") was field-blind — selecting Size still told the user
        "or YYYY-MM-DD" while selecting Creation Date still told them
        "type a number".
        """
        key = (
            "action_dialog.numeric_value_placeholder_date"
            if self._current_field() in _DATE_NUMERIC_FIELDS
            else "action_dialog.numeric_value_placeholder_number"
        )
        self._num_value_edit.setPlaceholderText(t(key))

    def _on_numeric_mode_toggled(self, button, checked: bool) -> None:
        # E7 from #351 (Wave 5): wired via QButtonGroup.buttonToggled,
        # which fires once per button per flip. Act on the
        # button-becoming-checked side only — without this guard we'd
        # apply twice (one off + one on) per user click.
        if not checked:
            return
        if button is self._num_mode_threshold_btn:
            self._numeric_mode = NUMERIC_MODE_THRESHOLD
        elif button is self._num_mode_topn_btn:
            self._numeric_mode = NUMERIC_MODE_TOPN
        else:
            return
        self._apply_numeric_sub_visibility()

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
        """Insert a regex token at the regex line edit's caret position.

        D6 from #350 (Wave 9a): the ``[abc]`` chip is a placeholder pattern —
        the user almost always wants to replace the inner ``abc`` with their
        own characters. After insertion, select the inner 3 chars so the
        user's next keystroke replaces them without manual selection.
        """
        self.regex.setFocus()
        cur = self.regex.cursorPosition()
        text = self.regex.text()
        new_text = text[:cur] + token + text[cur:]
        self.regex.setText(new_text)
        self.regex.setCursorPosition(cur + len(token))
        if token == "[abc]":
            # setSelection(start, length) — select "abc" (3 chars starting
            # 1 char past the inserted "[").
            self.regex.setSelection(cur + 1, 3)

    # ── Recent patterns ────────────────────────────────────────────────────

    def _show_recent_menu(self) -> None:
        # A6: gate by current field at render time — only show entries
        # recorded for this field (or legacy entries with field=None,
        # which apply to any field).
        current_field = self._current_field()
        visible = [
            (field, pat) for field, pat in self._recent_patterns
            if field is None or field == current_field
        ]
        menu = QMenu(self)
        if not visible:
            empty = menu.addAction(t("action_dialog.recent_empty"))
            empty.setEnabled(False)
        else:
            for _field, pat in visible:
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
        # A12 from #347: set self.regex FIRST, then flip the mode. If
        # the order were reversed, the mode flip's reverse-parse would
        # read the *outgoing* Simple state into self.regex, briefly
        # making the canonical pattern lie about what Apply will run.
        self.regex.setText(pattern)
        # A7: if the picked pattern is Simple-representable, flip to
        # Simple mode so the user sees the pattern in the familiar UI.
        # Otherwise land in Regex (as before).
        if self._match_fn is not None and self._mode_simple_btn.isEnabled():
            parsed = _try_parse_simple(pattern)
            if parsed is not None:
                if self._mode != MODE_SIMPLE:
                    self._mode_simple_btn.setChecked(True)
                else:
                    # Already Simple — re-apply visibility to refresh the inputs.
                    self._apply_mode_visibility()
            else:
                if self._mode != MODE_REGEX:
                    self._mode_regex_btn.setChecked(True)

    def _clear_recent_patterns(self) -> None:
        self._recent_patterns = []
        self._settings_set(_RECENT_KEY, [])

    def _record_recent_pattern(self, pattern: str) -> None:
        if not pattern:
            return
        # A13: strip before dedup so "IMG " and "IMG" don't appear as
        # separate entries and trailing-space user input self-cleans.
        pattern = pattern.strip()
        if not pattern:
            return
        # #397: with the pre-Apply gate removed, invalid regexes can
        # reach this method. Recent should only carry patterns the
        # user could productively re-pick — filter out anything that
        # won't compile. Numeric pseudo-patterns aren't recorded
        # (the caller in _emit_set_action gates on _field_panel_is_numeric)
        # so we only need to validate as a regex here.
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error:
            return
        field = self._current_field()
        entry: tuple[str | None, str] = (field, pattern)
        # Most-recent first, deduped by (field, pattern), capped.
        existing = [e for e in self._recent_patterns if e != entry]
        self._recent_patterns = ([entry] + existing)[:_RECENT_CAP]
        self._settings_set(_RECENT_KEY, self._recent_patterns)

    # ── Preview pane (only built when match_fn is supplied) ────────────────

    def _build_preview_pane(self) -> QWidget:
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # #391: preview-header row carries the preview label on the left
        # and the Reset window-size button on the right, replacing the
        # old close-row layout. Reset is visually associated with the
        # resizable surface (the preview pane itself) — the action it
        # performs (wipe geometry+splitter blobs) only affects this side.
        # #395 removed the test-against playground that used to live
        # above this header — live preview against real manifest data
        # covers the same iterative-tuning need.
        preview_header_row = QHBoxLayout()
        preview_header_row.setContentsMargins(0, 0, 0, 0)
        preview_header_row.addWidget(QLabel(t("action_dialog.preview_label")))
        preview_header_row.addStretch(1)
        preview_header_row.addWidget(self.btn_reset_geometry)
        right_layout.addLayout(preview_header_row)

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
        """Update ✓/✗ icon and friendly error label (informational only).

        In Simple mode the synthesised pattern is always valid (we
        re.escape the user's input), so the icon stays empty and we
        hide the error label.

        Empty regex → no icon, no error (neutral state). Valid regex →
        green ✓, error hidden. Invalid regex → red ✗ hidden + error
        label visible with the `re.error` message. The match counter
        falls back to an em dash while the regex is invalid.

        #397 dropped the Apply-button gating that this method used to
        perform: empty/invalid patterns no longer disable Apply. The
        receiver-side guards in
        ``file_operations.set_decision_by_regex`` surface invalid
        regex as a ``QMessageBox.warning`` and empty pattern as a
        ``QMessageBox.information("No matches")`` so the failure mode
        is visible at click-time rather than hidden behind a disabled
        button. The icon + error label remain as inline informational
        feedback while the user types.
        """
        if self._mode == MODE_SIMPLE or self._field_panel_is_numeric():
            self._set_status_icon(self._validation_icon, None)
            self._validation_icon.setAccessibleName("")
            # B11 (Wave 9a): clear toolTip alongside the icon so a stale
            # "Regex valid" tooltip from a prior validation doesn't linger.
            self._validation_icon.setToolTip("")
            self._validation_error.hide()
            return

        pattern = self.regex.text()
        if not pattern:
            self._set_status_icon(self._validation_icon, None)
            self._validation_icon.setAccessibleName("")
            self._validation_icon.setToolTip("")
            self._validation_error.hide()
            return

        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            # B3 from #348 (Wave 8): when the error label is shown the icon
            # is redundant — the prefixed "Invalid regex: ..." text already
            # tells the user what's wrong. Hide the icon so the row reads
            # cleaner (icon + error + Recent button previously crowded the
            # single row).
            self._set_status_icon(self._validation_icon, None)
            self._validation_icon.setAccessibleName(
                f"Regex invalid: {exc}"
            )
            # B11 (Wave 9a): icon is hidden on this branch (B3), so the
            # toolTip would never surface — clear it explicitly to match.
            self._validation_icon.setToolTip("")
            self._validation_error.setText(
                t("action_dialog.invalid_regex").format(error=str(exc))
            )
            self._validation_error.show()
            if self._match_fn is not None:
                self._match_counter.setText(
                    t("action_dialog.match_counter_invalid")
                )
            return

        self._set_status_icon(self._validation_icon, "valid")
        self._validation_icon.setAccessibleName("Regex valid")
        # B11 from #348 (Wave 9a): mirror accessibleName to toolTip so
        # sighted-but-non-screen-reader users get the same info on hover.
        # Only the valid path needs this — invalid path hides the icon
        # entirely (Wave 8 B3) so a toolTip would never surface.
        self._validation_icon.setToolTip("Regex valid")
        self._validation_error.hide()

    # ── Preview (debounced) ────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        """Pull live counts + sample names from the injected match_fn.

        Both modes funnel through this — the only difference is which
        widget produced the pattern (Regex mode uses self.regex, Simple
        mode synthesises via _build_pattern). Match-span highlighting is
        applied per-row by storing (start, end) on each list item; the
        delegate paints from there.

        Numeric panel uses its own counting path (groups + helpers)
        because the match_fn closure is regex-only.
        """
        if self._match_fn is None:
            return

        if self._field_panel_is_numeric():
            self._refresh_numeric_preview()
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
        # B9 (Wave 9b-trim): track for the post-Apply counter flash.
        self._last_matched_count = matched

        self._match_counter.setText(
            t("action_dialog.match_counter").format(matched=matched, total=total)
        )

        self._preview_list.clear()
        if matched == 0:
            self._preview_list.addItem(t("action_dialog.preview_empty"))
        else:
            # A2 from #347 (Wave 4): display the matched-field string,
            # not the basename, so highlight runs against the right text
            # for non-File-Name regexes (Folder / Score / Date / etc.).
            # For File Name field the two are identical.
            for _basename, matched_field_str in samples:
                item = QListWidgetItem(matched_field_str)
                if rx is not None:
                    m = rx.search(matched_field_str)
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

    def _refresh_numeric_preview(self) -> None:
        """Populate the preview pane + counter from the numeric panel.

        D5 + D8 from #350 (Wave 4): when Top-N is the active sub-mode,
        each preview row carries its group identifier and the value
        the ranking selected on. This makes the per-group semantic of
        Top-N visible (D5) and surfaces the tiebreaker context for
        equal-value records (D8 — same stable value-then-path order,
        but the user can now see the values that drove ordering).
        Threshold mode keeps the flat basename-only rendering since
        the threshold applies across the entire group set, not per-group.
        """
        from pathlib import Path
        from datetime import datetime

        field = self._current_field()
        total = sum(len(getattr(g, "items", [])) for g in self._groups)
        is_date = field in _DATE_NUMERIC_FIELDS

        def _fmt_value(val: float) -> str:
            if is_date:
                try:
                    return datetime.fromtimestamp(val).strftime("%Y-%m-%d")
                except (OSError, OverflowError, ValueError):
                    return f"{val:g}"
            return f"{val:g}"

        if self._numeric_mode == NUMERIC_MODE_TOPN:
            n = int(self._num_n_spin.value())
            order = str(self._num_order_combo.currentData() or "desc")
            rows = _select_top_n_with_metadata(self._groups, field, n, order)
            labels: list[str] = [
                f"Group {group_no} — {Path(path).name} ({_fmt_value(val)})"
                for group_no, path, val in rows
            ]
            # B6 from #348 (Wave 5): Top-N counter carries per-group
            # context — generic "X of Y match" loses the fact that
            # the operation is bounded to ≤N per group across G
            # groups. B7 from #348 is subsumed: the explicit (≤n per
            # group × group_count groups) text answers "what does N
            # mean here?" without an extra UI element.
            counter_text = t("action_dialog.match_counter_topn").format(
                matched=len(labels), n=n, group_count=len(self._groups),
            )
        else:
            op = str(self._num_cmp_combo.currentData() or ">")
            value_text = self._num_value_edit.text()
            paths = select_paths_by_threshold(
                self._groups, field, op, value_text
            )
            labels = [Path(p).name for p in paths]
            counter_text = t("action_dialog.match_counter").format(
                matched=len(labels), total=total,
            )

        matched = len(labels)
        # B9 (Wave 9b-trim): track for the post-Apply counter flash.
        self._last_matched_count = matched
        self._match_counter.setText(counter_text)

        self._preview_list.clear()
        if matched == 0:
            self._preview_list.addItem(t("action_dialog.preview_empty"))
        else:
            for label in labels[: self._sample_cap]:
                self._preview_list.addItem(QListWidgetItem(label))

        if matched > self._sample_cap:
            self._preview_truncated.setText(
                t("action_dialog.preview_truncated").format(
                    n=matched - self._sample_cap
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
        # #397 dropped the dialog-side gate. The receiver
        # (file_operations.set_decision_by_regex) surfaces invalid
        # regex as QMessageBox.warning and empty pattern as
        # QMessageBox.information("No matches"), so the failure mode
        # is visible at click-time rather than silently no-op'd by a
        # disabled button.
        field = self._current_field()
        if self._field_panel_is_numeric():
            pattern = self._build_numeric_pattern()
        else:
            pattern = self._build_pattern()
        idx = self._action_combo.currentIndex()
        _label, value = self._decisions[idx]
        # D3 from #350 (Wave 10): bulk-delete confirm gate. The "delete"
        # action moves files to the Recycle Bin via send2trash — it is
        # the only irreversible action in this dialog (keep / remove
        # from list / lock / unlock are all metadata-only or recoverable
        # via re-scan). Insert a confirmation modal before the emit so a
        # misfired click on a large batch lands on the safe path. Scope:
        # only "delete" + only when a live preview count exists
        # (match_fn supplied AND _last_matched_count populated). The
        # flat-layout branch (no preview, no count) skips the confirm —
        # there is no count to confirm against, and the downstream
        # receiver still emits its "Decision set to ..." status-bar
        # message. Known limitation (existing from A9/A10): the numeric
        # panel does not gate Apply on matched > 0, so the confirm could
        # surface "Delete 0 files" on a numeric-delete with no matches.
        # Document but don't fix in this wave — that's a pre-existing
        # gap, not a D3 regression.
        if (
            value == "delete"
            and self._match_fn is not None
            and self._last_matched_count is not None
        ):
            from app.views.dialogs.delete_regex_confirm_dialog import (
                DeleteRegexConfirmDialog,
            )
            confirmed = DeleteRegexConfirmDialog.ask(
                parent=self,
                matched=self._last_matched_count,
                pattern_summary=self._build_pattern_summary(),
            )
            if not confirmed:
                return  # User cancelled — no emit, no flash, no Recent.
        self.setActionRequested.emit(field, pattern, value)
        # B9 from #348 (Wave 9b-trim): flash "Applied to N rows" in the
        # match counter so the user gets in-dialog confirmation that the
        # action was applied. The receiver (file_operations.set_decision_
        # by_regex / execute_action_dialog._set_decision_by_regex) ALSO
        # emits "Decision set to ..." on the main-window status bar
        # (#316/#318) — these two surfaces complement each other rather
        # than duplicating the same emit path. The counter's normal text
        # is restored organically on the next preview refresh (any
        # subsequent typing / mode toggle / field change retriggers the
        # debounced preview).
        if self._match_fn is not None and self._last_matched_count is not None:
            self._match_counter.setText(
                t("action_dialog.match_counter_applied").format(
                    matched=self._last_matched_count
                )
            )
        # Recent-patterns only records raw regex strings — numeric
        # pseudo-patterns (`__cmp__:`, `__top_n__:`) would be confusing
        # in the Recent dropdown which lives in the regex panel.
        # A9 from #347: record AFTER successful emit, not before, so
        # broken patterns never enter Recent and persist across sessions.
        if not self._field_panel_is_numeric():
            self._record_recent_pattern(self._build_pattern())

    def _build_numeric_pattern(self) -> str:
        """Encode the active numeric sub-panel as a pattern string."""
        if self._numeric_mode == NUMERIC_MODE_TOPN:
            n = int(self._num_n_spin.value())
            order = self._num_order_combo.currentData() or "desc"
            return encode_top_n_pattern(n, str(order))
        op = self._num_cmp_combo.currentData() or ">"
        value_text = self._num_value_edit.text()
        return encode_cmp_pattern(str(op), value_text)

    def _build_pattern_summary(self) -> str:
        """Human-readable description of the current pattern for D3's confirm.

        D3 from #350 (Wave 10): the delete-confirm modal needs a pattern
        description the user actually wrote — not the synthesised regex
        the Simple panel hides. For Simple mode, surface the op + text;
        for Regex mode, the raw pattern; for numeric, the comparison
        expression. Uses the same translation keys both locales share.
        """
        field_display = _field_display(self._current_field())
        if self._field_panel_is_numeric():
            if self._numeric_mode == NUMERIC_MODE_TOPN:
                n = int(self._num_n_spin.value())
                order_key = str(self._num_order_combo.currentData() or "desc")
                order_label = t(
                    "action_dialog.numeric_order_top"
                    if order_key == "desc"
                    else "action_dialog.numeric_order_bottom"
                )
                return t(
                    "action_dialog.pattern_summary_numeric_topn",
                    order=order_label,
                    n=n,
                    field=field_display,
                )
            op = str(self._num_cmp_combo.currentData() or ">")
            return t(
                "action_dialog.pattern_summary_numeric_threshold",
                field=field_display,
                op=op,
                value=self._num_value_edit.text(),
            )
        if self._mode == MODE_SIMPLE:
            op_key = self._simple_op_combo.currentData() or "contains"
            op_label = t(f"action_dialog.simple_op_{op_key}")
            return t(
                "action_dialog.pattern_summary_simple",
                field=field_display,
                op=op_label,
                text=self._simple_text.text(),
            )
        return t(
            "action_dialog.pattern_summary_regex",
            field=field_display,
            pattern=self.regex.text(),
        )

    def _set_default_field(self, field_name: str) -> None:
        try:
            idx = self.combo.findData(field_name)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _on_field_changed(self, _index: int) -> None:
        # A1 from #347: preserve user-typed regex across field changes.
        # Compare the current text to the prior field's auto-default;
        # only overwrite when they match (or the field is empty). A
        # user who typed `\d{4}-\d{2}` against File Name and then
        # mis-clicked the combo would otherwise lose their work with
        # no undo.
        #
        # A1-ext from #347: order swap. Apply the new pre-fill FIRST
        # (when allowed), then call _apply_mode_visibility so Simple
        # reverse-parses the new canonical value rather than the stale
        # one. The previous comment justified the visibility-first
        # order via a "spurious preview refresh" concern, but
        # combo.currentIndexChanged already triggers _preview_timer
        # independently — the order swap is a no-op for the preview
        # timer and a win for Simple-panel consistency.
        prev_default = re.escape(self._row_values.get(self._previous_field, ""))
        current_text = self.regex.text()
        if current_text == prev_default or current_text == "":
            self._apply_exact_regex_for_current_field()
        self._apply_mode_visibility()
        # A5 from #347: refresh the numeric placeholder so date fields
        # get the date hint and number fields get the number hint.
        self._update_numeric_value_placeholder()
        # A4 from #347 (Wave 5): the same text can parse differently
        # for Date vs Number fields, so re-validate on every field
        # change to keep the ✓/✗ icon honest.
        self._validate_threshold()
        # Validation icon would otherwise read the (now-irrelevant)
        # regex value in numeric-panel mode; suppress it explicitly.
        self._validate_regex()
        self._previous_field = self._current_field()

    def _apply_exact_regex_for_current_field(self) -> None:
        # B10 from #348: emit a "contains" pattern (no ^/$ anchors).
        # The reverse-parser in _try_parse_simple then naturally lifts
        # this into (op="contains", text=value) — matching the
        # documented default Simple op ("most-useful starting state").
        # Pre-B10, this method stamped ^X$ which reverse-parsed as
        # "exact", silently overriding the documented default.
        field = self._current_field()
        value = self._row_values.get(field, "")
        if value:
            self.regex.setText(re.escape(value))
        else:
            self.regex.clear()

    def _reset_geometry(self) -> None:
        """E5 from #351 (Wave 8): clear persisted geometry + splitter blobs
        and immediately resize the dialog back to the hardcoded defaults.

        Only the geometry keys in ``window_state.ini`` are wiped —
        ``ui.action_dialog.{context_id}.{mode,field,simple_op}`` in
        ``settings.json`` are untouched, so the user's mode/field/op
        preferences survive a window-size reset (they're conceptually
        separate from chrome size).
        """
        if self._splitter is None:
            return
        try:
            store = window_state_qsettings()
            store.remove(QSETTINGS_KEY_ACTION_DIALOG_GEOM)
            store.remove(QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE)
            store.sync()
        except Exception:
            # Mirror save_widget_geometry's swallow — a read-only INI
            # shouldn't block the user from at least the in-memory reset.
            logger.warning(
                "ActionDialog: failed to clear persisted geometry keys"
            )
        # Apply the hardcoded defaults in-memory so the user sees the
        # reset immediately (without needing to close-and-reopen).
        self.resize(self.minimumSize())
        self._splitter.setSizes([420, 380])

    def done(self, result: int) -> None:
        """Persist geometry, splitter state, simple_op, and field on close.

        Geometry + splitter state only save when the preview pane is wired
        up (match_fn given), because that's the only branch that runs the
        resizable QSplitter layout — the flat layout has no user-resizable
        geometry to preserve (E4 invariant).

        C13 from #349 (Wave 8): splitter handle position now persists in
        addition to the outer-window geometry. Pre-Wave-8 only the window
        rect was saved, so the user's [pane-A | pane-B] balance reset to
        [420, 380] every time the dialog reopened.

        E3: simple_op persisted under per-context key at close time.
        E8: field persisted under per-context key at close time.
        """
        if self._match_fn is not None:
            save_widget_geometry(self, QSETTINGS_KEY_ACTION_DIALOG_GEOM)
        if self._splitter is not None:
            save_splitter_state(
                self._splitter, QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE
            )
        # E3: persist simple_op so it restores on next open.
        op_data = self._simple_op_combo.currentData()
        if op_data:
            self._settings_set(self._simple_op_key, op_data)
        # E8: persist current field so it restores on next open.
        current_field = self._current_field()
        if current_field:
            self._settings_set(self._field_key, current_field)
        super().done(result)


# Backward-compatibility alias
SelectDialog = ActionDialog
