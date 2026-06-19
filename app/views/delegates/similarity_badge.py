"""Similarity-badge delegate for the results tree's column 0 (file rows).

The UX baseline audit flagged the Similarity column as overloading five
distinct meanings — Ref / 100% / N% / N*% / "—" — as undifferentiated
plain text, decodable only via hover tooltip. This delegate encodes each
as a coloured, shaped pill so the column self-explains at a glance:

    Ref   gold pill, ★ prefix    the group's chosen keeper
    100%  purple pill            byte-identical duplicate
    95%   blue pill              direct near-match
    97*%  gray *dashed* pill     transitive / indirect member (lower confidence)
    —     muted gray pill        no comparable image (e.g. Live Photo MOV)

Colour-blind / grayscale safety: the pills don't rely on hue alone — the
text inside each (``Ref`` / ``100%`` / ``95%`` / ``97*%`` / ``—``) stays
distinct in grayscale, and the passenger state additionally uses a dashed
border (a shape cue, not a colour cue).

Group-header rows (no valid parent) and any cell missing the kind role
fall back to the default painter, so the "Group N" label renders normally
on its band.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from app.views.constants import (
    SIM_EXACT,
    SIM_NEAR,
    SIM_NONE,
    SIM_PASSENGER,
    SIM_REF,
    SIMILARITY_KIND_ROLE,
)
from app.views.theme import DAYLIGHT

# Per-kind pill styling (text colour, fill, border, dashed?). Warm-
# harmonised with the Daylight palette but distinct in hue *and* — for the
# passenger state — in border shape.
_BADGE_STYLES: dict[int, dict[str, object]] = {
    SIM_REF: {"text": "#a85f2e", "bg": "#f7ebda", "border": "#e0c79f", "dashed": False},
    SIM_EXACT: {"text": "#6d54b3", "bg": "#eee9fa", "border": "#d2c4f0", "dashed": False},
    SIM_NEAR: {"text": "#2f6fb0", "bg": "#e7f1fb", "border": "#bcd6ee", "dashed": False},
    SIM_PASSENGER: {"text": "#7a7060", "bg": "#fbf7ef", "border": "#bcae95", "dashed": True},
    SIM_NONE: {"text": "#8a8073", "bg": "#efe7d9", "border": "#d8cdb9", "dashed": False},
}

_PILL_PAD_H = 8     # horizontal text padding inside the pill
_PILL_MAX_H = 22    # pill never taller than this regardless of row height
_PILL_LEFT = 6      # gap from the cell's left edge
_PILL_RADIUS = 5


class SimilarityBadgeDelegate(QStyledItemDelegate):
    """Paint the column-0 similarity cell of a *file* row as a coloured pill."""

    def _badge_for(self, index) -> tuple[str, dict[str, object]] | None:
        """Return (label, style) when this index is a paintable file-row
        badge, else ``None`` (caller falls back to the default painter)."""
        if not index.parent().isValid():
            return None
        kind = index.data(SIMILARITY_KIND_ROLE)
        text = index.data(Qt.DisplayRole) or ""
        if kind is None or not text:
            return None
        style = _BADGE_STYLES.get(kind)
        if style is None:
            return None
        label = f"★ {text}" if kind == SIM_REF else text
        return label, style

    def paint(self, painter: QPainter, option, index) -> None:  # type: ignore[override]
        badge = self._badge_for(index)
        if badge is None:
            super().paint(painter, option, index)
            return
        label, style = badge

        painter.save()
        # Background: drawRow already painted the row band / delete tint for
        # the unselected case, so we only fill for selection / hover to match
        # what the default delegate paints in the sibling columns.
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(DAYLIGHT["select_bg"]))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor(DAYLIGHT["bg_subtle"]))

        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)

        text_w = painter.fontMetrics().horizontalAdvance(label)
        pill_w = min(text_w + _PILL_PAD_H * 2, max(option.rect.width() - _PILL_LEFT - 2, 0))
        pill_h = min(option.rect.height() - 6, _PILL_MAX_H)
        x = option.rect.left() + _PILL_LEFT
        y = option.rect.top() + (option.rect.height() - pill_h) / 2.0
        pill = QRectF(x, y, pill_w, pill_h)

        pen = QPen(QColor(str(style["border"])))
        pen.setWidthF(1.0)
        if style["dashed"]:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(str(style["bg"])))
        painter.drawRoundedRect(pill, _PILL_RADIUS, _PILL_RADIUS)

        painter.setPen(QColor(str(style["text"])))
        painter.drawText(pill, Qt.AlignCenter, label)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # type: ignore[override]
        size = super().sizeHint(option, index)
        if self._badge_for(index) is not None:
            # Reserve room for the pill padding (+ the Ref star) so
            # ResizeToContents doesn't clip the badge; row height follows
            # the active density.
            from app.views import density
            return QSize(size.width() + 30, density.row_height())
        return size
