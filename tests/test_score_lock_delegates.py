"""Tests for the score-bar and lock-icon delegates.

Both read a semantic value off the model (score fraction / lock bool) and
turn it into a painted cell. The classification helpers are tested
directly; ``paint`` is exercised by rendering to a QImage and asserting
the expected mark lands (and, for the negative cases, that it does not).
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QStyleOptionViewItem

from app.views.constants import COL_GROUP, COL_LOCK, COL_SCORE
from app.views.delegates.lock_icon import LockIconDelegate
from app.views.delegates.score_bar import ScoreBarDelegate
from app.views.theme import DAYLIGHT
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


def _render(delegate, index, col_w=120):
    img = QImage(col_w, 28, QImage.Format_ARGB32)
    img.fill(Qt.white)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, col_w, 28)
    opt.font = QFont()
    painter = QPainter(img)
    delegate.paint(painter, opt, index)
    painter.end()
    return img


def _count_color(img, color: QColor) -> int:
    return sum(
        1
        for x in range(img.width())
        for y in range(img.height())
        if QColor(img.pixel(x, y)) == color
    )


def _count_warm_fill(img) -> int:
    """Pixels that look like the warm score-bar gradient (r>g>b, mid-tone).

    Robust to the gradient + antialiasing: the fill colours #cdab86 and
    #b8946a both satisfy this; the lighter track (#ece4d6, r=236) and the
    muted dash glyph are excluded, so a positive count means the *fill*
    (not just the track) was painted.
    """
    n = 0
    for x in range(img.width()):
        for y in range(img.height()):
            c = QColor(img.pixel(x, y))
            r, g, b = c.red(), c.green(), c.blue()
            if 140 <= r <= 220 and g < r - 10 and b < g:
                n += 1
    return n


# ── ScoreBarDelegate ─────────────────────────────────────────────────────────


def _score_model():
    items = [
        _rec(file_path="/p/ref.jpg", score=0.9, phash="ffffffffffffffff"),
        _rec(file_path="/p/live.MOV", score=None, phash=None),
    ]
    return build_model([_group(items)])[0]


def test_score_helper_reads_fraction_and_none(qapp):
    model = _score_model()
    delegate = ScoreBarDelegate()
    grp = model.item(0, COL_GROUP)
    assert delegate._score(grp.child(0, COL_SCORE).index()) == 0.9      # scored
    assert delegate._score(grp.child(1, COL_SCORE).index()) is None     # unscored (MOV)
    assert delegate._score(model.index(0, COL_SCORE)) is None           # group row


def test_score_sizehint_widens_file_rows(qapp):
    model = _score_model()
    delegate = ScoreBarDelegate()
    grp = model.item(0, COL_GROUP)
    opt = QStyleOptionViewItem()
    opt.font = QFont()
    assert delegate.sizeHint(opt, grp.child(0, COL_SCORE).index()).width() >= 96


def test_score_bar_fill_is_painted(qapp):
    model = _score_model()
    delegate = ScoreBarDelegate()
    grp = model.item(0, COL_GROUP)
    img = _render(delegate, grp.child(0, COL_SCORE).index())
    assert _count_warm_fill(img) > 0


def test_unscored_row_paints_no_bar(qapp):
    model = _score_model()
    delegate = ScoreBarDelegate()
    grp = model.item(0, COL_GROUP)
    img = _render(delegate, grp.child(1, COL_SCORE).index())   # MOV → dash only
    assert _count_warm_fill(img) == 0


# ── LockIconDelegate ─────────────────────────────────────────────────────────


def _lock_model():
    locked = _rec(file_path="/p/locked.jpg", is_locked=True)
    unlocked = _rec(file_path="/p/open.jpg", is_locked=False)
    return build_model([_group([locked, unlocked])])[0]


def test_lock_helper(qapp):
    model = _lock_model()
    delegate = LockIconDelegate()
    grp = model.item(0, COL_GROUP)
    assert delegate._is_locked(grp.child(0, COL_LOCK).index()) is True
    assert delegate._is_locked(grp.child(1, COL_LOCK).index()) is False
    assert delegate._is_locked(model.index(0, COL_LOCK)) is False


def test_lock_icon_painted_only_when_locked(qapp):
    model = _lock_model()
    delegate = LockIconDelegate()
    grp = model.item(0, COL_GROUP)
    accent = QColor(DAYLIGHT["accent"])
    locked_img = _render(delegate, grp.child(0, COL_LOCK).index(), col_w=40)
    open_img = _render(delegate, grp.child(1, COL_LOCK).index(), col_w=40)
    assert _count_color(locked_img, accent) > 0
    assert _count_color(open_img, accent) == 0


# ── LockIconDelegate — click toggle + header icon (design-review refinements) ──


def test_lock_click_emits_toggle(qapp):
    from PySide6.QtCore import QEvent, QPointF, QRect
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QStyleOptionViewItem

    model = _lock_model()
    delegate = LockIconDelegate()
    grp = model.item(0, COL_GROUP)
    locked_index = grp.child(0, COL_LOCK).index()   # starts locked
    captured: list[bool] = []
    delegate.lockToggled.connect(lambda _idx, state: captured.append(state))

    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 40, 26)
    ev = QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(20, 13),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier,
    )
    assert delegate.editorEvent(ev, model, opt, locked_index) is True
    assert captured == [False]   # locked row toggles to unlocked


def test_make_lock_icon_renders(qapp):
    from app.views.delegates.lock_icon import make_lock_icon
    icon = make_lock_icon("#bd6b39")
    assert not icon.isNull()
