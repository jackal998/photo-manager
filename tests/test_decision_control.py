"""Tests for the inline decision-control delegate.

Covers the three things that can break independently: the segment model
(values + which one is active for a given decision), the click hit-test
(``editorEvent`` → ``decisionPicked`` with the right value), and that
paint actually fills the active segment.
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QStyleOptionViewItem

from app.views.constants import COL_ACTION, COL_GROUP, IGNORE_DECISION
from app.views.delegates.decision_control import DecisionControlDelegate
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


def _model(decision="delete"):
    return build_model([_group([_rec(user_decision=decision)])])[0]


def test_segments_are_the_three_real_decisions(qapp):
    delegate = DecisionControlDelegate()
    values = [v for v, _label in delegate._segments()]
    assert values == ["", "delete", IGNORE_DECISION]


def test_is_active_maps_empty_to_keep(qapp):
    d = DecisionControlDelegate()
    assert d._is_active("", "") is True
    assert d._is_active("", "keep") is True          # legacy value
    assert d._is_active("", "delete") is False
    assert d._is_active("delete", "delete") is True
    assert d._is_active(IGNORE_DECISION, IGNORE_DECISION) is True
    assert d._is_active("delete", "") is False


def test_segment_rects_are_three_and_ordered(qapp):
    d = DecisionControlDelegate()
    rects = d._segment_rects(QRect(0, 0, 240, 26))
    assert len(rects) == 3
    assert rects[0].left() < rects[1].left() < rects[2].left()
    assert rects[0].right() <= rects[1].left()        # no overlap


def test_click_on_delete_segment_emits_delete(qapp):
    model = _model(decision="")
    delegate = DecisionControlDelegate()
    grp = model.item(0, COL_GROUP)
    action_index = grp.child(0, COL_ACTION).index()

    captured: list[tuple[object, str]] = []
    delegate.decisionPicked.connect(lambda idx, val: captured.append((idx, val)))

    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 240, 26)
    opt.font = QFont()
    delete_center = delegate._segment_rects(opt.rect)[1].center()
    event = QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(delete_center),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
    )
    handled = delegate.editorEvent(event, model, opt, action_index)

    assert handled is True
    assert len(captured) == 1
    assert captured[0][1] == "delete"


def test_click_on_remove_segment_emits_ignore(qapp):
    model = _model(decision="")
    delegate = DecisionControlDelegate()
    grp = model.item(0, COL_GROUP)
    action_index = grp.child(0, COL_ACTION).index()
    captured: list[tuple[object, str]] = []
    delegate.decisionPicked.connect(lambda idx, val: captured.append((idx, val)))

    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 240, 26)
    opt.font = QFont()
    remove_center = delegate._segment_rects(opt.rect)[2].center()
    event = QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(remove_center),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
    )
    delegate.editorEvent(event, model, opt, action_index)
    assert captured and captured[0][1] == IGNORE_DECISION


def test_group_row_click_is_ignored(qapp):
    model = _model()
    delegate = DecisionControlDelegate()
    group_index = model.index(0, COL_ACTION)
    captured = []
    delegate.decisionPicked.connect(lambda idx, val: captured.append(val))
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 240, 26)
    opt.font = QFont()
    event = QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(20, 13),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
    )
    assert delegate.editorEvent(event, model, opt, group_index) is False
    assert captured == []


def test_paint_fills_active_delete_segment_red(qapp):
    model = _model(decision="delete")
    delegate = DecisionControlDelegate()
    grp = model.item(0, COL_GROUP)
    action_index = grp.child(0, COL_ACTION).index()

    img = QImage(240, 26, QImage.Format_ARGB32)
    img.fill(Qt.white)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 240, 26)
    opt.font = QFont()
    painter = QPainter(img)
    delegate.paint(painter, opt, action_index)
    painter.end()

    red = QColor("#c4503f")
    hits = sum(
        1
        for x in range(240)
        for y in range(26)
        if QColor(img.pixel(x, y)) == red
    )
    assert hits > 0, "active Delete segment was not filled"
