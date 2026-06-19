"""Inline decision-control delegate for the Action column.

Renders each file row's decision as a clickable 3-segment control —
Keep / Delete / Remove — with the active segment filled in its state
colour (green / red / muted). Clicking a segment sets that row's decision
directly, so the user triages without opening the right-click menu.

Three segments, not the prototype's four: this app's decision model
(#584) is ``{'' , 'delete', 'ignore'}`` — ``''`` *is* keep / no-action,
so "Keep" and "Undecided" are the same stored value. A fourth Undecided
button would be a second control for one state, so it's omitted; an
unset row simply shows Keep as the active segment.

Clicks are handled in ``editorEvent`` (hit-testing the segment rects) and
surfaced via the ``decisionPicked(index, value)`` signal — no per-row
QWidget is created, so this stays cheap on large libraries. The wiring
(``MainWindow``) maps the index to a path and calls
``file_operations.set_decision`` — the same single-row path the
right-click menu uses, which intentionally bypasses the bulk lock
pre-filter (a deliberate per-row click is not a bulk op).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.views.constants import DECISION_ROLE, IGNORE_DECISION
from app.views.theme import DAYLIGHT
from infrastructure.i18n import t

# Per-segment styling when active. Inactive segments share one quiet style.
_ACTIVE_STYLE: dict[str, dict[str, str]] = {
    "": {"bg": "#e7f3ec", "border": "#bcdcc7", "text": "#2f8a5a"},          # Keep
    "delete": {"bg": "#c4503f", "border": "#c4503f", "text": "#ffffff"},     # Delete
    IGNORE_DECISION: {"bg": "#efe7d9", "border": "#ddd2bf", "text": "#7a7060"},  # Remove
}
_INACTIVE_BORDER = "#e0d6c4"
_INACTIVE_TEXT = "#a89f8f"

_PAD = 4      # outer left/right padding inside the cell
_GAP = 3      # gap between segments
_SEG_H = 22   # segment height cap
_RADIUS = 5


class DecisionControlDelegate(QStyledItemDelegate):
    """Paint + handle the inline Keep/Delete/Remove control on file rows."""

    decisionPicked = Signal(object, str)  # (view/proxy QModelIndex, decision value)

    def _segments(self) -> list[tuple[str, str]]:
        """(decision value, short label) for each segment, in display order."""
        return [
            ("", t("decision.keep_short")),
            ("delete", t("decision.delete_short")),
            (IGNORE_DECISION, t("decision.remove_short")),
        ]

    def _segment_rects(self, rect) -> list[QRectF]:
        n = 3
        avail = rect.width() - _PAD * 2 - _GAP * (n - 1)
        seg_w = max(avail / n, 0)
        h = min(rect.height() - 6, _SEG_H)
        y = rect.top() + (rect.height() - h) / 2.0
        rects: list[QRectF] = []
        x = rect.left() + _PAD
        for _ in range(n):
            rects.append(QRectF(x, y, seg_w, h))
            x += seg_w + _GAP
        return rects

    @staticmethod
    def _is_active(seg_value: str, decision: str) -> bool:
        if seg_value == "":
            # "" is keep/no-action; legacy manifests may store "keep".
            return decision in ("", "keep")
        return decision == seg_value

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        if not index.parent().isValid():
            super().paint(painter, option, index)
            return
        decision = index.data(DECISION_ROLE) or ""

        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(DAYLIGHT["select_bg"]))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor(DAYLIGHT["bg_subtle"]))

        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont(option.font)
        font.setPointSizeF(max(option.font.pointSizeF() - 0.5, 7.0))
        painter.setFont(font)

        for (value, label), rect in zip(self._segments(), self._segment_rects(option.rect)):
            active = self._is_active(value, decision)
            if active:
                style = _ACTIVE_STYLE[value]
                painter.setBrush(QColor(style["bg"]))
                painter.setPen(QColor(style["border"]))
                painter.drawRoundedRect(rect, _RADIUS, _RADIUS)
                painter.setPen(QColor(style["text"]))
            else:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QColor(_INACTIVE_BORDER))
                painter.drawRoundedRect(rect, _RADIUS, _RADIUS)
                painter.setPen(QColor(_INACTIVE_TEXT))
            painter.drawText(rect, Qt.AlignCenter, label)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # type: ignore[override]
        if not index.parent().isValid():
            return False
        if (
            event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
        ):
            pos = event.position().toPoint()
            for (value, _label), rect in zip(self._segments(), self._segment_rects(option.rect)):
                if rect.contains(pos):
                    self.decisionPicked.emit(index, value)
                    return True
        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        base = super().sizeHint(option, index)
        if not index.parent().isValid():
            return base
        fm = QFontMetrics(option.font)
        seg_w = max(fm.horizontalAdvance(lbl) for _v, lbl in self._segments()) + 16
        total = int(seg_w * 3 + _GAP * 2 + _PAD * 2)
        return QSize(max(total, base.width()), max(base.height(), 26))
