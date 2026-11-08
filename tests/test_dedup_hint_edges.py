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

from scanner.dedup import HINT_ONLY_PREFIX, HashResult, classify
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

    def test_cross_moment_matches_are_hints_that_name_their_partner(self):
        """GROUPING "10": cross-moment matches are hints. No REVIEW row is
        written, and each grouped moment row names its other moment as text."""
        rows = _rows(classify(self._records(), source_priority=PRIORITY))
        assert not [r for r in rows.values() if r.action == "REVIEW_DUPLICATE"]
        # Byte copies are EXACT and take no hint; each moment keeper names the
        # other moments as text.
        keepers = [r for r in rows.values() if r.action != "EXACT"]
        assert len(keepers) == 3
        for row in keepers:
            assert row.group_id is not None
            assert "hint: low-confidence match with" in row.reason, row.reason
            assert " h apart" in row.reason, row.reason


class TestLeafAttach:
    """A lone file whose only edge is low joins a two-member group. The target
    is high-cored: two byte-identical copies (a high sha edge)."""

    def test_lone_file_attaches_as_low_leaf(self):
        records = [
            _hr("/a/grp_a.jpg", "ga", A, T1, "src_a"),
            _hr("/b/grp_a_copy.jpg", "ga", A, T1, "src_b"),
            _hr("/c/lone.jpg", "gx", S, T_PLUS_36H, "src_a"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        lone = rows["/c/lone.jpg"]
        assert lone.match_confidence == "low"
        assert lone.group_id is None
        assert HINT_ONLY_PREFIX in lone.reason, lone.reason
        assert _group_sizes(rows) == [2]


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
        # GROUPING 10: the low leaf never joins either pair; both pairs stay
        # high cores, and the middle file is an isolated hint.
        assert _group_sizes(rows) == [2, 2]
        assert rows["/c/middle.jpg"].group_id is None
        assert rows["/a/grp_a.jpg"].group_id == rows["/a/grp_a_copy.jpg"].group_id
        assert rows["/b/grp_b.jpg"].group_id == rows["/b/grp_b_copy.jpg"].group_id

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
        assert _group_sizes(rows) == []
        for path in ("/a/solo_x.jpg", "/c/solo_y.jpg"):
            assert rows[path].group_id is None
            assert rows[path].match_confidence == "low"


# Screenshot pHashes for the chain tests: consecutive links are within the
# threshold (8), every non-consecutive pair is beyond it (>= 16). Both files
# are PNG, so every link is low (both-screenshots demotion).
SHOT = {
    "s1": "00000000ffffffff",
    "s2": "00000000ffffff00",
    "s3": "00000000ffff0000",
    "s4": "00000000ff000000",
}


def _shot(name: str) -> HashResult:
    return _hr(f"/s/{name}.png", f"sha_{name}", SHOT[name], T1, "src_a", file_type="png")


class TestHintChainStopsAtLonePairs:
    """Low-only links never chain: a low-only pair does not grow."""

    def test_four_screenshot_chain_gives_two_pairs_not_one_group(self):
        records = [_shot(n) for n in ("s1", "s2", "s3", "s4")]
        rows = _rows(classify(records, source_priority=PRIORITY))
        # GROUPING 10: both-PNG links are hints, so the chain forms no group.
        assert _group_sizes(rows) == []
        assert all(r.group_id is None for r in rows.values())

    def test_chain_result_is_identical_when_record_order_is_shuffled(self):
        base = _rows(classify([_shot(n) for n in ("s1", "s2", "s3", "s4")],
                              source_priority=PRIORITY))
        expected = {k: r.group_id for k, r in base.items()}
        recs = [_shot(n) for n in ("s1", "s2", "s3", "s4")]
        random.Random(0).shuffle(recs)
        shuffled = _rows(classify(recs, source_priority=PRIORITY))
        assert {k: r.group_id for k, r in shuffled.items()} == expected

    def test_lone_file_hinting_a_low_only_pair_stays_isolated(self):
        # s2 is low-linked to s1 (pair) and to x; x must not join the pair.
        records = [_shot("s1"), _shot("s2"), _hr("/s/x.png", "sha_x", SHOT["s3"],
                                                  T1, "src_a", file_type="png")]
        rows = _rows(classify(records, source_priority=PRIORITY))
        x = rows["/s/x.png"]
        assert x.group_id is None
        assert x.duplicate_of is None
        assert "s2.png" in x.reason
        assert x.match_confidence == "low"
        assert all(rows[p].group_id is None for p in ("/s/s1.png", "/s/s2.png"))


def _lone_hint(exif_date: datetime | None) -> list[HashResult]:
    """s1-s2 low pair plus x low to s2 only: x is an isolated hint."""
    return [
        _shot("s1"),
        _shot("s2"),
        _hr("/s/x.png", "sha_x", SHOT["s3"], exif_date, "src_a", file_type="png"),
    ]


class TestIsolatedHintIsUndecided:
    """(h) GROUPING 6: an isolated hint row carries no dedup action."""

    def test_isolated_hint_row_has_undecided_action_and_marker(self):
        rows = _rows(classify(_lone_hint(T1), source_priority=PRIORITY))
        x = rows["/s/x.png"]
        assert x.group_id is None
        assert x.action == ""
        assert x.match_confidence == "low"
        assert HINT_ONLY_PREFIX in x.reason, x.reason
        assert "s2.png" in x.reason

    def test_undated_isolated_hint_becomes_undated(self):
        rows = _rows(classify(_lone_hint(None), source_priority=PRIORITY))
        x = rows["/s/x.png"]
        assert x.group_id is None
        assert x.action == "UNDATED"
        assert HINT_ONLY_PREFIX in x.reason, x.reason


class TestUngroupedRowWithOnlyHintsIsUndecided:
    """(i) GROUPING 10: a file whose only links are low-confidence hints is
    ungrouped and carries no dedup action, even beside a grouped pair."""

    def test_ungrouped_row_with_only_hints_stays_undecided(self):
        """x's only links are hints (shot time disagrees with both moments), so
        it is ungrouped with an undecided action."""
        records = [
            _hr("/a/a1.jpg", "ga", A, T1, "src_a"),
            _hr("/a/a2.jpg", "ga", A, T1, "src_a"),
            _hr("/b/b1.jpg", "gb", B, T1, "src_b"),
            _hr("/b/b2.jpg", "gb", B, T1, "src_b"),
            _hr("/c/x.jpg", "gx", S, T_PLUS_36H, "src_a"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        x = rows["/c/x.jpg"]
        assert x.group_id is None
        assert x.action == ""
        assert HINT_ONLY_PREFIX in x.reason, x.reason


def _chain_hash(k: int) -> str:
    """64-bit pHash with ones from bit 8k upward. Steps k→k+1 differ by 8 bits,
    any two steps apart by 16, so only consecutive links are within threshold."""
    mask = (1 << 64) - 1
    return f"{mask ^ ((1 << (8 * k)) - 1):016x}"


def _png(path: str, sha: str, k: int) -> HashResult:
    return _hr(path, sha, _chain_hash(k), T1, "src_a", file_type="png")


class TestHintsDoNotChainThroughALeaf:
    """H1 — a leaf attached by a low edge never anchors the next lone file."""

    def test_six_screenshot_chain_with_copy_is_two_two_three(self):
        records = [_png(f"/s/s{k}.png", f"sha_s{k}", k) for k in range(1, 7)]
        records.append(_png("/s/s1_copy.png", "sha_s1", 1))  # byte copy of s1
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert max(_group_sizes(rows)) < 7, "no group of 7 through leaves"
        # GROUPING 10: only the byte-copy pair is a high core; the chain links
        # are hints and form no group.
        assert _group_sizes(rows) == [2]
        assert rows["/s/s1_copy.png"].group_id == rows["/s/s1.png"].group_id
        assert rows["/s/s2.png"].group_id is None

    def test_six_screenshot_chain_without_copy_is_two_two_two(self):
        records = [_png(f"/s/s{k}.png", f"sha_s{k}", k) for k in range(1, 7)]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == []

    def test_core_leaf_far_shape_leaves_far_as_isolated_hint(self):
        records = [
            _png("/s/a.png", "ga", 1),
            _png("/s/a_copy.png", "ga", 1),      # core {a, a_copy}: high sha edge
            _png("/s/b.png", "gb", 2),           # leaf: low to a only
            _png("/s/c.png", "gc", 3),           # far: low to b only
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]
        far = rows["/s/c.png"]
        assert far.group_id is None
        assert far.action == ""
        assert HINT_ONLY_PREFIX in far.reason, far.reason
        assert rows["/s/b.png"].group_id is None


def _raw(path: str, sha: str, exif_date: datetime) -> HashResult:
    return _hr(path, sha, A, exif_date, "src_a", file_type="raw")


class TestComplementaryRawPairsRespectShotTime:
    """M1 — a RAW+RAW bucket with an identical pHash (no dedup decision) groups
    only when the shot times agree."""

    def test_disagreeing_times_pair_as_a_low_hint_not_a_certain_group(self):
        """Two lone RAWs with disagreeing shot times form one 2-row hint pair:
        the low edge between the two cluster anchors pairs them, exactly as the
        all-lossy branch and the near-dup path pair a time-disagreeing match.
        Rows stay undecided (no dedup action)."""
        records = [
            _raw("/r/x1.dng", "r1", T1),
            _raw("/r/x2.dng", "r2", T1 + timedelta(hours=5, minutes=10)),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        # GROUPING 10: the disagreeing-time link is a hint, so no group forms.
        assert _group_sizes(rows) == []
        for path in ("/r/x1.dng", "/r/x2.dng"):
            assert rows[path].group_id is None
            assert rows[path].action == ""
            assert HINT_ONLY_PREFIX in rows[path].reason, rows[path].reason

    def test_cr2_and_its_conversion_stay_together_across_a_moment(self):
        """#824's own example: x.cr2 and its DNG conversion agree on time with
        each other but not with m0 (an earlier moment, same pHash). The
        conversion pair is a high edge, so they group. m0 is a lone file whose
        only link is low to x.cr2, a high member, so the hint rule attaches it:
        the outcome is one group of three, with m0 as the hint leaf."""
        later = T1 + timedelta(hours=5, minutes=13)
        records = [
            _raw("/a/m0.dng", "m0", T1),
            _raw("/b/x.cr2", "xc", later),
            _raw("/c/x_conv.dng", "xd", later),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert rows["/b/x.cr2"].group_id == rows["/c/x_conv.dng"].group_id
        assert rows["/b/x.cr2"].group_id is not None
        assert _group_sizes(rows) == [2]
        # GROUPING 10: m0 is a hint only, so it stays out of the group.
        m0 = rows["/a/m0.dng"]
        assert m0.group_id is None
        assert m0.match_confidence == "low"
        assert "x.cr2" in m0.reason

    def test_undated_member_does_not_anchor_every_moment(self):
        """F1: an undated RAW sorting first (/0/z.dng) must not become the
        anchor of the whole bucket. Dated members form the clusters first, and
        the undated member joins the first one: the shape is [2,2,2] (m0 with z,
        x.cr2 with x_conv, y.dng with y.jpg), not one group of six. The renamed
        /f/z.dng gives the same shape."""
        later = T1 + timedelta(hours=5, minutes=13)
        for z_path in ("/0/z.dng", "/f/z.dng"):
            records = [
                _raw(z_path, "zz", None),
                _raw("/a/m0.dng", "m0", T1),
                _raw("/b/x.cr2", "xc", later),
                _raw("/c/x_conv.dng", "xd", later),
                _raw("/d/y.dng", "yd", T1 + timedelta(hours=9, minutes=7)),
                _hr("/e/y.jpg", "yj", A, T1 + timedelta(hours=9, minutes=7),
                    "src_a", file_type="jpeg"),
            ]
            rows = _rows(classify(records, source_priority=PRIORITY))
            assert _group_sizes(rows) == [2, 2, 2], z_path
            assert rows[z_path].group_id == rows["/a/m0.dng"].group_id

    def test_raw_and_edited_jpeg_at_same_time_group(self):
        """Complementary RAW+lossy at one shot time stays one high group."""
        records = [
            _raw("/b/x.dng", "xr", T1),
            _hr("/c/x_edit.jpg", "xj", A, T1, "src_a", file_type="jpeg"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]

    def test_agreeing_times_group_as_one_shot(self):
        records = [_raw("/r/x1.dng", "r1", T1), _raw("/r/x2.dng", "r2", T1)]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]
