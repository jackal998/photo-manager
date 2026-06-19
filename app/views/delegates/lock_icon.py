"""Lock-state delegate — a painted padlock instead of the 🔒 emoji.

The baseline used a 🔒 emoji glyph in an otherwise monochrome text UI
(the audit flagged the visual-language inconsistency). This paints a
small warm-accent padlock centred in COL_LOCK for locked file rows, and
nothing for unlocked rows (the column reads quiet at its typical state).
Lock state is read from SORT_ROLE (1 = locked / 0 = unlocked), kept in
sync by TreeController.update_lock_cells. Group-header rows fall back to
the default painter.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.views.constants import SORT_ROLE
from app.views.theme import DAYLIGHT


class LockIconDelegate(QStyledItemDelegate):
    """Paint a padlock in COL_LOCK for locked file rows."""

    def _is_locked(self, index) -> bool:
        if not index.parent().isValid():
            return False
        return bool(index.data(SORT_ROLE))

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        if not index.parent().isValid():
            super().paint(painter, option, index)
            return

        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(DAYLIGHT["select_bg"]))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor(DAYLIGHT["bg_subtle"]))

        if not self._is_locked(index):
            painter.restore()
            return

        painter.setRenderHint(QPainter.Antialiasing, True)
        cx = option.rect.center().x() + 0.5
        cy = option.rect.center().y() + 0.5
        accent = QColor(DAYLIGHT["accent"])

        # Shackle (top semicircle arc)
        pen = QPen(accent)
        pen.setWidthF(1.3)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        shackle = QRectF(cx - 3.2, cy - 5.2, 6.4, 6.4)
        painter.drawArc(shackle, 0, 180 * 16)

        # Body (rounded rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        body = QRectF(cx - 4.5, cy - 2.0, 9.0, 7.0)
        painter.drawRoundedRect(body, 1.6, 1.6)
        painter.restore()
