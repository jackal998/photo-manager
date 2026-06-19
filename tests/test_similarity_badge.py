"""Tests for the similarity-badge delegate.

The classification gate (``_badge_for``) decides which cells become pills
and what label/colour each gets — a wrong answer means a wrong badge, so
it's tested directly against a real model. ``paint`` is exercised by
rendering to a QImage and asserting the pill fill actually lands on the
canvas (catches a delegate that silently paints nothing).
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QStyleOptionViewItem

from app.views.constants import COL_GROUP
from app.views.delegates.similarity_badge import _BADGE_STYLES, SimilarityBadgeDelegate
from app.views.tree_model_builder import build_model


def _rec(**overrides):
    base = dict(
        file_path="/photos/a.jpg", folder_path="/photos", file_size_bytes=1,
        action="", user_decision="", is_locked=False, hamming_distance=None,
        shot_date=None, creation_date=None, pixel_width=None, pixel_height=None,
        phash=None, score=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _group(items, group_number=1):
    return SimpleNamespace(group_number=group_number, items=items)


def _model_with_five_states():
    items = [
        _rec(file_path="/p/ref.jpg", action="", score=0.9, phash="ffffffffffffffff"),
        _rec(file_path="/p/live.MOV", action="", score=None, phash=None),
        _rec(file_path="/p/exact.jpg", action="EXACT", score=0.8, phash="ffffffffffffffff"),
        _rec(file_path="/p/near.jpg", action="REVIEW_DUPLICATE", score=0.7, phash="fffffffffffffff8"),
        _rec(file_path="/p/raw.jpg", action="", score=0.5, phash="fffffffffffffff0"),
    ]
    return build_model([_group(items)])[0]


def test_group_row_is_not_a_badge(qapp):
    model = _model_with_five_states()
    delegate = SimilarityBadgeDelegate()
    group_index = model.index(0, COL_GROUP)
    assert delegate._badge_for(group_index) is None


def test_ref_label_gets_star_prefix(qapp):
    model = _model_with_five_states()
    delegate = SimilarityBadgeDelegate()
    grp = model.item(0, COL_GROUP)
    ref_index = grp.child(0, COL_GROUP).index()
    badge = delegate._badge_for(ref_index)
    assert badge is not None
    label, _style = badge
    assert label.startswith("★")
    assert "Ref" in label


def test_every_file_row_resolves_to_a_known_style(qapp):
    model = _model_with_five_states()
    delegate = SimilarityBadgeDelegate()
    grp = model.item(0, COL_GROUP)
    for r in range(grp.rowCount()):
        badge = delegate._badge_for(grp.child(r, COL_GROUP).index())
        assert badge is not None, f"row {r} should be a badge"
        _label, style = badge
        assert style in _BADGE_STYLES.values()


def test_sizehint_reserves_pill_padding(qapp):
    model = _model_with_five_states()
    delegate = SimilarityBadgeDelegate()
    grp = model.item(0, COL_GROUP)
    opt = QStyleOptionViewItem()
    opt.font = QFont()
    file_index = grp.child(2, COL_GROUP).index()   # the EXACT row
    group_index = model.index(0, COL_GROUP)
    assert delegate.sizeHint(opt, file_index).width() > delegate.sizeHint(opt, group_index).width()


def test_paint_actually_fills_the_pill(qapp):
    """Render the EXACT badge to a white canvas and assert its fill colour
    lands somewhere — a non-painting delegate would leave pure white."""
    model = _model_with_five_states()
    delegate = SimilarityBadgeDelegate()
    grp = model.item(0, COL_GROUP)
    exact_index = grp.child(2, COL_GROUP).index()

    img = QImage(140, 28, QImage.Format_ARGB32)
    img.fill(Qt.white)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 140, 28)
    opt.font = QFont()
    painter = QPainter(img)
    delegate.paint(painter, opt, exact_index)
    painter.end()

    target = QColor(str(_BADGE_STYLES[1]["bg"]))  # SIM_EXACT fill
    hits = sum(
        1
        for x in range(140)
        for y in range(28)
        if QColor(img.pixel(x, y)) == target
    )
    assert hits > 0, "similarity badge fill never painted"
