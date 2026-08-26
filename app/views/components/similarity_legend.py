"""A static legend explaining the five similarity-badge states.

The UX audit noted the Similarity column's five meanings had no legend —
two (``—`` and ``N*%``) only decoded via hover tooltip. This builds a
compact, always-visible key for the preview pane footer, reusing the
exact badge colours from the similarity delegate so the swatches match
the cells one-to-one (single source of truth for the palette).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.views.constants import SIM_EXACT, SIM_NEAR, SIM_NONE, SIM_PASSENGER, SIM_REF
from app.views.delegates.similarity_badge import _BADGE_STYLES
from app.views.theme import DAYLIGHT
from infrastructure.i18n import t

# (kind, representative pill text, description i18n key) in display order.
_ROWS = [
    (SIM_REF, "★ Ref", "preview.legend.ref"),
    (SIM_EXACT, "100%", "preview.legend.exact"),
    (SIM_NEAR, "95%", "preview.legend.near"),
    (SIM_PASSENGER, "97∗", "preview.legend.passenger"),
    (SIM_NONE, "—", "preview.legend.none"),
]


def _pill(kind: int, text: str) -> QLabel:
    style = _BADGE_STYLES[kind]
    border_style = "dashed" if style["dashed"] else "solid"
    pill = QLabel(text)
    pill.setAlignment(Qt.AlignCenter)
    pill.setFixedWidth(60)
    pill.setStyleSheet(
        f"color:{style['text']};"
        f"background:{style['bg']};"
        f"border:1px {border_style} {style['border']};"
        "border-radius:5px; padding:1px 4px; font-weight:600;"
    )
    return pill


def build_similarity_legend() -> QWidget:
    """Return the legend widget (a titled column of swatch + description rows)."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(5)

    title = QLabel(t("preview.legend.title"))
    title.setStyleSheet(
        f"color:{DAYLIGHT['text_muted']}; font-weight:600; "
        "text-transform:uppercase; letter-spacing:1px;"
    )
    layout.addWidget(title)

    for kind, pill_text, desc_key in _ROWS:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(_pill(kind, pill_text))
        desc = QLabel(t(desc_key))
        desc.setStyleSheet(f"color:{DAYLIGHT['text_muted']};")
        desc.setWordWrap(True)
        row_layout.addWidget(desc, 1)
        layout.addWidget(row)

    return widget
