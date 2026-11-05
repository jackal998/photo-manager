"""GROUPING_STRATEGY_VERSION "6" — low-confidence edges are hints.

A low-confidence pHash edge (shot times disagree, or both PNG screenshots) may
attach a lone file to a group as a leaf. It must never merge two groups that
each already have two or more members, otherwise unrelated moments of one scene
chain into one group (the owner's "torii" DNGs, 2026-11-05 research §5.1 T3).

Hash constants are 32-set-bit pHashes, well inside the #516 entropy guard:
    A  = 00000000ffffffff
    B  = 00000000ffff0000   d(A, B) = 16  (> threshold 10: never an edge)
    S  = 00000000ffffff00   d(S, A) = d(S, B) = 8  (a bridge within threshold)
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from scanner.dedup import HashResult, classify
from scanner.walker import FileRecord

A = "00000000ffffffff"
B = "00000000ffff0000"
S = "00000000ffffff00"

T1 = datetime(2024, 6, 12, 20, 38, 0)            # torii moment 1
T_NEXT_MORNING = datetime(2024, 6, 13, 10, 24, 0)  # 13 h 46 min later: disagrees
T_17_02 = datetime(2024, 6, 12, 17, 2, 0)        # 3 h 36 min before T1: disagrees
T_PLUS_36H = T1 + timedelta(hours=36)            # disagrees

PRIORITY = {"src_a": 0, "src_b": 1}


def _hr(
    path: str,
    sha256: str,
    phash: str = A,
    exif_date: datetime | None = T1,
    source_label: str = "src_a",
    file_type: str = "jpeg",
) -> HashResult:
    return HashResult(
        record=FileRecord(path=Path(path), source_label=source_label, file_type=file_type),
        sha256=sha256,
        phash=phash,
        dhash=A,  # dHash agrees everywhere, so only the shot-time gate decides
        mean_color=None,
        exif_date=exif_date,
        pixel_width=4000,
        pixel_height=3000,
    )


def _rows(result: list) -> dict:
    return {Path(r.source_path).as_posix(): r for r in result}


def _group_sizes(rows: dict) -> list[int]:
    return sorted(Counter(r.group_id for r in rows.values() if r.group_id).values())


class TestTorii:
    """Three moments of one scene, each two byte-identical copies."""

    def _records(self) -> list[HashResult]:
        return [
            _hr("/a/torii_0638.jpg", "m1", A, T1, "src_a"),
            _hr("/b/torii_0638_copy.jpg", "m1", A, T1, "src_b"),
            _hr("/a/torii_1024.jpg", "m2", "00000000fffffffc", T_NEXT_MORNING, "src_a"),
            _hr("/b/torii_1024_copy.jpg", "m2", "00000000fffffffc", T_NEXT_MORNING, "src_b"),
            _hr("/a/torii_1702.jpg", "m3", "00000000ffffffcf", T_17_02, "src_a"),
            _hr("/b/torii_1702_copy.jpg", "m3", "00000000ffffffcf", T_17_02, "src_b"),
        ]

    def test_three_moments_stay_three_groups_of_two(self):
        rows = _rows(classify(self._records(), source_priority=PRIORITY))
        assert _group_sizes(rows) == [2, 2, 2]

    def test_cross_moment_rows_are_low_review_and_name_their_partner(self):
        rows = _rows(classify(self._records(), source_priority=PRIORITY))
        review = [r for r in rows.values() if r.action == "REVIEW_DUPLICATE"]
        assert review, "cross-moment near-dups must still be flagged for review"
        for row in review:
            assert row.match_confidence == "low"
            assert row.duplicate_of is not None
            assert Path(row.duplicate_of).name in row.reason
            assert " h apart" in row.reason


class TestLeafAttach:
    """A lone file whose only edge is low joins a two-member group."""

    def test_lone_file_attaches_as_low_leaf(self):
        records = [
            _hr("/a/grp_a.jpg", "ga", A, T1, "src_a"),
            _hr("/b/grp_a_copy.jpg", "ga", A, T1, "src_b"),
            _hr("/c/lone.jpg", "gx", S, T_PLUS_36H, "src_a"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        lone = rows["/c/lone.jpg"]
        assert lone.match_confidence == "low"
        assert lone.group_id is not None
        assert lone.group_id == rows["/a/grp_a.jpg"].group_id
        assert _group_sizes(rows) == [3]


class TestNoChainingThroughALeaf:
    """Lone file s is a low hint to two separate pairs A and B."""

    def _records(self, order: list[int] | None = None) -> list[HashResult]:
        recs = [
            _hr("/a/grp_a.jpg", "ga", A, T1, "src_a"),
            _hr("/a/grp_a_copy.jpg", "ga", A, T1, "src_a"),
            _hr("/b/grp_b.jpg", "gb", B, T1, "src_a"),
            _hr("/b/grp_b_copy.jpg", "gb", B, T1, "src_a"),
            _hr("/c/middle.jpg", "gs", S, T_PLUS_36H, "src_a"),
        ]
        if order is not None:
            recs = [recs[i] for i in order]
        return recs

    def test_leaf_joins_exactly_one_group_by_sorted_edge_order(self):
        rows = _rows(classify(self._records(), source_priority=PRIORITY))
        assert _group_sizes(rows) == [2, 3]
        middle = rows["/c/middle.jpg"]
        # The sorted low pairs put (/a/grp_a.jpg, /c/middle.jpg) before
        # (/b/grp_b.jpg, /c/middle.jpg), so the A pair takes the leaf.
        assert middle.group_id == rows["/a/grp_a.jpg"].group_id
        assert rows["/b/grp_b.jpg"].group_id == rows["/b/grp_b_copy.jpg"].group_id
        assert rows["/b/grp_b.jpg"].group_id != middle.group_id

    def test_result_is_identical_when_record_order_is_shuffled(self):
        base = _rows(classify(self._records(), source_priority=PRIORITY))
        expected = {k: r.group_id for k, r in base.items()}
        order = list(range(5))
        random.Random(0).shuffle(order)
        shuffled = _rows(classify(self._records(order), source_priority=PRIORITY))
        assert {k: r.group_id for k, r in shuffled.items()} == expected
        reversed_rows = _rows(
            classify(self._records(list(reversed(range(5)))), source_priority=PRIORITY)
        )
        assert {k: r.group_id for k, r in reversed_rows.items()} == expected


class TestHighEdgesUnchanged:
    """A time-agreeing, pHash-identical pair still merges two pairs (high)."""

    def test_high_pair_merges_two_two_member_groups_into_four(self):
        records = [
            _hr("/a/grp_a.jpg", "ga", A, T1, "src_a"),
            _hr("/a/grp_a_copy.jpg", "ga", A, T1, "src_a"),
            _hr("/b/grp_b.jpg", "gb", A, T1, "src_b"),
            _hr("/b/grp_b_copy.jpg", "gb", A, T1, "src_b"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [4]
        assert rows["/b/grp_b.jpg"].match_confidence == "high"


class TestTwoSingletonsByLowHint:
    """Nobody else is nearby: a low hint alone still groups two files."""

    def test_two_singletons_joined_only_by_low_edge_form_one_pair(self):
        records = [
            _hr("/a/solo_x.jpg", "sx", A, T1, "src_a"),
            _hr("/c/solo_y.jpg", "sy", S, T_PLUS_36H, "src_a"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]
        assert rows["/c/solo_y.jpg"].match_confidence == "low"
        assert rows["/c/solo_y.jpg"].group_id == rows["/a/solo_x.jpg"].group_id
