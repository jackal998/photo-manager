"""Keep-worthiness score delegate — a mini progress bar + the number.

Renders COL_SCORE of each file row as a small warm-gradient bar (the
score's 0.00–1.00 fraction) with the numeric value right-aligned beside
it, so the eye can rank keep-worthiness down a group without reading
every digit. Unscored rows (Live Photo MOV passengers, isolated files,
pre-#187 manifests) carry the negative sentinel in SORT_ROLE and render
as a plain muted "—". Group-header rows fall back to the default painter.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.views.constants import SORT_ROLE
from app.views.theme import DAYLIGHT

_TRACK = "#ece4d6"
_FILL_A = "#cdab86"
_FILL_B = "#b8946a"
_BAR_H = 6
_PAD = 6


class ScoreBarDelegate(QStyledItemDelegate):
    """Paint COL_SCORE of a file row as a mini bar + number."""

    def _score(self, index) -> float | None:
        """The row's score in [0, 1], or None when unscored / not a file row."""
        if not index.parent().isValid():
            return None
        raw = index.data(SORT_ROLE)
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0.0 else None

    def _paint_state_bg(self, painter, option) -> None:
        # Selection only — the design has no row-hover state.
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(DAYLIGHT["select_bg"]))

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        if not index.parent().isValid():
            super().paint(painter, option, index)
            return
        score = self._score(index)
        painter.save()
        self._paint_state_bg(painter, option)

        if score is None:
            painter.setPen(QColor(DAYLIGHT["text_faint"]))
            painter.setFont(option.font)
            painter.drawText(option.rect, Qt.AlignCenter, "—")
            painter.restore()
            return

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(option.font)
        rect = option.rect
        num = f"{score:.2f}"
        # Number width measured from the actual font so it never clips (a
        # narrow saved column width was making it vanish). The number is
        # ALWAYS drawn; the bar is drawn only when there's room left for it.
        num_w = painter.fontMetrics().horizontalAdvance(num) + 8
        num_rect = QRectF(rect.right() - _PAD - num_w, rect.top(), num_w, rect.height())

        bar_left = rect.left() + _PAD
        bar_right = num_rect.left() - _PAD
        bar_w = bar_right - bar_left
        if bar_w >= 24:
            bar_y = rect.top() + (rect.height() - _BAR_H) / 2.0
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(_TRACK))
            painter.drawRoundedRect(QRectF(bar_left, bar_y, bar_w, _BAR_H), 3, 3)
            fill_w = bar_w * max(0.0, min(1.0, score))
            if fill_w > 0:
                grad = QLinearGradient(bar_left, 0, bar_left + bar_w, 0)
                grad.setColorAt(0.0, QColor(_FILL_A))
                grad.setColorAt(1.0, QColor(_FILL_B))
                painter.setBrush(grad)
                painter.drawRoundedRect(QRectF(bar_left, bar_y, fill_w, _BAR_H), 3, 3)

        # Number — primary text colour (was muted, which read as "missing"
        # against the row) and clamped inside the cell so it can't clip.
        painter.setPen(QColor(DAYLIGHT["text"]))
        painter.drawText(num_rect, Qt.AlignRight | Qt.AlignVCenter, num)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        size = super().sizeHint(option, index)
        if index.parent().isValid():
            # Reserve room for the bar + the number so ResizeToContents
            # doesn't collapse the Score column to just the digits; row
            # height follows the active density.
            from app.views import density
            return QSize(max(size.width(), 96), density.row_height())
        return size
