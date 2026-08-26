"""Lock-state delegate — a clickable padlock instead of the 🔒 emoji.

Every file row shows a padlock in COL_LOCK: a faint outline when unlocked
(an affordance — click to lock) and a solid warm-accent lock when locked.
Clicking the cell toggles the row's lock state (emitted via
``lockToggled`` and applied by MainWindow through
``file_operations.set_locked_state``). Lock state is read from SORT_ROLE
(1 = locked / 0 = unlocked), kept in sync by
``TreeController.update_lock_cells``. Group-header rows fall back to the
default painter.

``make_lock_icon`` renders the same padlock to a QIcon so the COL_LOCK
column *header* can show a lock glyph instead of the word "Lock".
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.views.constants import SORT_ROLE
from app.views.theme import DAYLIGHT

_UNLOCKED_COLOR = "#cdbfa6"  # faint warm gray — "click to lock" affordance


def _paint_lock(painter: QPainter, cx: float, cy: float, color: QColor, filled: bool) -> None:
    """Draw a small padlock centred at (cx, cy). Solid body when ``filled``,
    outline-only body otherwise (the faint unlocked state)."""
    pen = QPen(color)
    pen.setWidthF(1.3)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    # Shackle (top semicircle)
    painter.drawArc(QRectF(cx - 3.2, cy - 5.2, 6.4, 6.4), 0, 180 * 16)
    # Body
    body = QRectF(cx - 4.5, cy - 2.0, 9.0, 7.0)
    if filled:
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
    else:
        painter.setBrush(Qt.NoBrush)  # outline body for the faint state
    painter.drawRoundedRect(body, 1.6, 1.6)


def make_lock_icon(color_hex: str, px: int = 16) -> QIcon:
    """A solid padlock QIcon for the COL_LOCK column header."""
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    _paint_lock(painter, px / 2.0, px / 2.0 + 1.0, QColor(color_hex), filled=True)
    painter.end()
    return QIcon(pm)


class LockIconDelegate(QStyledItemDelegate):
    """Paint a clickable padlock in COL_LOCK for file rows."""

    lockToggled = Signal(object, bool)  # (view/proxy QModelIndex, new locked state)

    def _is_locked(self, index) -> bool:
        if not index.parent().isValid():
            return False
        return bool(index.data(SORT_ROLE))

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        if not index.parent().isValid():
            super().paint(painter, option, index)
            return

        painter.save()
        # Selection only — the design has no row-hover state.
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(DAYLIGHT["select_bg"]))

        painter.setRenderHint(QPainter.Antialiasing, True)
        cx = option.rect.center().x() + 0.5
        cy = option.rect.center().y() + 0.5
        locked = self._is_locked(index)
        color = QColor(DAYLIGHT["accent"]) if locked else QColor(_UNLOCKED_COLOR)
        _paint_lock(painter, cx, cy, color, filled=locked)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # type: ignore[override]
        if not index.parent().isValid():
            return False
        if (
            event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
            and option.rect.contains(event.position().toPoint())
        ):
            self.lockToggled.emit(index, not self._is_locked(index))
            return True
        return super().editorEvent(event, model, option, index)
