"""Tests for the row-density module + that delegates honour it.

The density module is a tiny global; the load-bearing behaviour is that a
delegate's sizeHint height actually changes when density flips (otherwise
the View → Density menu would do nothing).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QStyleOptionViewItem

from app.views import density
from app.views.constants import COL_GROUP
from app.views.delegates.similarity_badge import SimilarityBadgeDelegate
from app.views.tree_model_builder import build_model


@pytest.fixture(autouse=True)
def _reset_density():
    """Density is process-global; restore the default after each test so
    one test's switch can't leak into another."""
    yield
    density.set_density(density.DEFAULT)


def test_default_is_comfortable():
    assert density.current() == "comfortable"
    assert density.row_height() == 30


def test_set_known_density():
    density.set_density("compact")
    assert density.current() == "compact"
    assert density.row_height() == 22


def test_unknown_density_falls_back_to_default():
    density.set_density("enormous")
    assert density.current() == density.DEFAULT


def test_available_lists_both():
    codes = [c for c, _key in density.available()]
    assert codes == ["comfortable", "compact"]


def test_delegate_height_follows_density(qapp):
    rec = SimpleNamespace(
        file_path="/p/a.jpg", folder_path="/p", file_size_bytes=1, action="",
        user_decision="", is_locked=False, hamming_distance=None, shot_date=None,
        creation_date=None, pixel_width=None, pixel_height=None, phash=None, score=0.5,
    )
    grp = SimpleNamespace(group_number=1, items=[rec])
    # Keep `model` alive in scope — a GC'd QStandardItemModel leaves the
    # index dangling and the sizeHint call would crash.
    model = build_model([grp])[0]
    index = model.item(0, COL_GROUP).child(0, COL_GROUP).index()
    delegate = SimilarityBadgeDelegate()
    opt = QStyleOptionViewItem()
    opt.font = QFont()

    density.set_density("comfortable")
    tall = delegate.sizeHint(opt, index).height()
    density.set_density("compact")
    short = delegate.sizeHint(opt, index).height()

    assert tall == 30
    assert short == 22
    assert short < tall
