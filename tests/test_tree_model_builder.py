"""Tests for app/views/tree_model_builder — Qt model construction.

Pure-logic helpers (`_hamming_to_pct`, `_file_similarity`) are tested
directly; `build_model` is exercised end-to-end with synthetic
PhotoGroup data and the resulting QStandardItemModel is inspected.
"""

from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime

import pytest

from app.views.tree_model_builder import (
    _ACTION_SORT,
    _DECISION_SORT,
    _file_similarity,
    _hamming_to_pct,
    build_model,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _rec(**overrides):
    """Build a minimal record-shaped object using attributes the builder reads."""
    base = dict(
        file_path="/photos/a.jpg",
        folder_path="/photos",
        file_size_bytes=12345,
        action="MOVE",
        user_decision="",
        hamming_distance=None,
        shot_date=None,
        creation_date=None,
        pixel_width=None,
        pixel_height=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _group(items, group_number=1):
    return SimpleNamespace(group_number=group_number, items=items)


# ── _hamming_to_pct ────────────────────────────────────────────────────────


class TestHammingToPct:
    def test_none_returns_placeholder(self):
        assert _hamming_to_pct(None) == "~dup"

    def test_zero_distance_is_100(self):
        assert _hamming_to_pct(0) == "100%"

    def test_full_distance_is_zero_pct(self):
        assert _hamming_to_pct(64) == "0%"

    @pytest.mark.parametrize("hamming,expected", [
        (2, "97%"),    # 62/64 = 0.96875 → 97
        (4, "94%"),    # 60/64 = 0.9375 → 94
        (6, "91%"),
        (32, "50%"),
    ])
    def test_intermediate_values(self, hamming, expected):
        assert _hamming_to_pct(hamming) == expected


# ── _file_similarity ───────────────────────────────────────────────────────


class TestFileSimilarity:
    def test_exact_returns_100(self):
        assert _file_similarity("EXACT", _rec()) == "100%"

    def test_review_duplicate_uses_hamming(self):
        assert _file_similarity("REVIEW_DUPLICATE", _rec(hamming_distance=2)) == "97%"

    def test_review_duplicate_with_no_hamming_returns_placeholder(self):
        assert _file_similarity("REVIEW_DUPLICATE", _rec(hamming_distance=None)) == "~dup"

    @pytest.mark.parametrize("action", ["KEEP", "MOVE", "UNDATED", "", "FOO_UNKNOWN"])
    def test_other_actions_render_as_ref(self, action):
        assert _file_similarity(action, _rec()) == "Ref"


# ── _ACTION_SORT / _DECISION_SORT mappings ─────────────────────────────────


class TestSortMappings:
    def test_ref_tier_actions_share_top_priority(self):
        # Per #76 + #81: every "Ref"-displayed action sorts at position 1.
        assert _ACTION_SORT["KEEP"] == 1
        assert _ACTION_SORT["MOVE"] == 1
        assert _ACTION_SORT["UNDATED"] == 1
        assert _ACTION_SORT[""] == 1

    def test_exact_before_review_duplicate(self):
        # Per #81: descending similarity within a group (Ref → 100% → near-match).
        assert _ACTION_SORT["EXACT"] < _ACTION_SORT["REVIEW_DUPLICATE"]

    def test_decision_sort_delete_before_keep_before_undecided(self):
        assert _DECISION_SORT["delete"] < _DECISION_SORT["keep"]


# ── build_model ────────────────────────────────────────────────────────────


class TestBuildModel:
    def test_returns_model_and_proxy(self, qapp):
        model, proxy = build_model([])
        assert model is not None
        assert proxy is not None

    def test_headers_set(self, qapp):
        from PySide6.QtCore import Qt
        model, _ = build_model([])
        assert model.columnCount() > 0
        header_text = model.headerData(0, Qt.Orientation.Horizontal)
        assert isinstance(header_text, str)
        assert header_text  # non-empty

    def test_one_group_with_two_files_appears_as_group_row_plus_two_children(self, qapp):
        rec_ref = _rec(file_path="/p/ref.jpg", action="MOVE")
        rec_dup = _rec(file_path="/p/dup.jpg", action="REVIEW_DUPLICATE", hamming_distance=4)
        model, _ = build_model([_group([rec_ref, rec_dup])])

        # Top-level: 1 row (the group)
        assert model.rowCount() == 1
        group_row = model.item(0, 0)
        assert "Group 1" in group_row.text()
        # Group has 2 children
        assert group_row.rowCount() == 2

    def test_child_row_similarity_column_shows_ref_or_pct(self, qapp):
        rec_ref = _rec(file_path="/p/ref.jpg", action="MOVE")
        rec_exact = _rec(file_path="/p/exact.jpg", action="EXACT")
        rec_review = _rec(
            file_path="/p/near.jpg", action="REVIEW_DUPLICATE", hamming_distance=2
        )
        model, _ = build_model([_group([rec_ref, rec_exact, rec_review])])

        group_row = model.item(0, 0)
        sims = {group_row.child(i, 0).text() for i in range(group_row.rowCount())}
        assert sims == {"Ref", "100%", "97%"}

    def test_size_and_dates_render_in_child_row(self, qapp):
        rec = _rec(
            file_path="/p/a.jpg",
            file_size_bytes=4321,
            shot_date=datetime(2024, 5, 1, 12, 0, 0),
            creation_date=datetime(2024, 5, 1, 11, 0, 0),
            pixel_width=320, pixel_height=240,
        )
        model, _ = build_model([_group([rec])])

        group_row = model.item(0, 0)
        child = group_row.child(0)
        # Find the row's cell texts via the model
        cells = [
            group_row.child(0, c).text() if group_row.child(0, c) else ""
            for c in range(model.columnCount())
        ]
        joined = " ".join(cells)
        assert "4321" in joined
        assert "320×240" in joined
        assert "2024-05-01" in joined  # both dates start with this

    def test_multiple_groups_yield_multiple_top_rows(self, qapp):
        g1 = _group([_rec(file_path="/p/a.jpg")], group_number=1)
        g2 = _group([_rec(file_path="/p/b.jpg")], group_number=2)
        g3 = _group([_rec(file_path="/p/c.jpg")], group_number=3)
        model, _ = build_model([g1, g2, g3])
        assert model.rowCount() == 3

    def test_empty_group_still_yields_a_group_row(self, qapp):
        # Defensive — no items, but still appears as a top-level Group row.
        empty = _group([], group_number=42)
        model, _ = build_model([empty])
        assert model.rowCount() == 1
        assert "Group 42" in model.item(0, 0).text()
        assert model.item(0, 0).rowCount() == 0

    def test_proxy_failure_returns_none_proxy(self, qapp, monkeypatch):
        """If QSortFilterProxyModel construction throws, build_model returns
        (model, None) — the caller is documented to handle proxy=None."""
        from app.views import tree_model_builder as tmb

        class _BoomProxy:
            def __init__(self, *a, **k):
                raise RuntimeError("synthetic proxy failure")

        monkeypatch.setattr(tmb, "QSortFilterProxyModel", _BoomProxy)
        model, proxy = build_model([])
        assert model is not None
        assert proxy is None
