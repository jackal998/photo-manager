"""Tests for the similarity legend widget."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from app.views.components.similarity_legend import _pill, build_similarity_legend
from app.views.constants import SIM_EXACT, SIM_PASSENGER


def test_legend_lists_all_five_states(qapp):
    widget = build_similarity_legend()
    texts = [lbl.text() for lbl in widget.findChildren(QLabel)]
    for pill in ("★ Ref", "100%", "95%", "97∗", "—"):
        assert pill in texts, f"legend missing swatch {pill!r}"


def test_passenger_pill_is_dashed_others_solid(qapp):
    assert "dashed" in _pill(SIM_PASSENGER, "97∗").styleSheet()
    assert "solid" in _pill(SIM_EXACT, "100%").styleSheet()


def test_pill_uses_its_badge_colours(qapp):
    from app.views.delegates.similarity_badge import _BADGE_STYLES
    sheet = _pill(SIM_EXACT, "100%").styleSheet()
    assert str(_BADGE_STYLES[SIM_EXACT]["bg"]) in sheet
    assert str(_BADGE_STYLES[SIM_EXACT]["text"]) in sheet
