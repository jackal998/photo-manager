"""GROUPING_STRATEGY_VERSION "7" — same-moment bursts become a keep-all stack.

A burst is a group whose frames are one shot apart (every consecutive gap
within ``BURST_MAX_GAP_S``) but whose span is longer than that: a GoPro or
compact-camera series, one frame per second. ``_mark_burst_stacks`` relabels
its frames to undecided ("") at high confidence, so no frame is a delete
candidate. It adds no edge and merges nothing; group_id is untouched.

Pins: a burst becomes one keep-all stack; a frame first paired low still joins;
byte copies keep EXACT; a format duplicate inside the burst joins; a span within
the gap is untouched; an undated bridge is not a burst; a hint leaf is never a
frame; Live-Photo video peers are ignored by detection; the result is independent
of record order; group_id stays the lex-min path.

Hash constants (each step is 2 bits; the whole chain stays within threshold 10):
    P0 = 00000000ffffffff   P1 = 00000001fffffffe   P2 = 00000003fffffffc
    P3 = 00000007fffffff8   P4 = 0000000ffffffff0
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

from scanner.dedup import BURST_PREFIX, HashResult, classify
from scanner.walker import FileRecord
from tests.test_dedup_hint_edges import _hr, _rows

P0 = "00000000ffffffff"
P1 = "00000001fffffffe"
P2 = "00000003fffffffc"
P3 = "00000007fffffff8"
P4 = "0000000ffffffff0"

T0 = datetime(2024, 6, 12, 20, 38, 0)
PRIORITY = {"src_a": 0, "src_b": 1}


def _at(sec: float) -> datetime:
    return T0 + timedelta(seconds=sec)


def _frame(name: str, sha: str, phash: str, sec: float, label: str = "src_a",
           file_type: str = "jpeg") -> HashResult:
    return _hr(f"/a/{name}", sha, phash, _at(sec), label, file_type)


def _five_frames() -> list[HashResult]:
    """One frame per second-pair: t0, +2, +4, +6, +8 s, consecutive pHash 2 bits."""
    return [
        _frame("f0.jpg", "s0", P0, 0),
        _frame("f1.jpg", "s1", P1, 2),
        _frame("f2.jpg", "s2", P2, 4),
        _frame("f3.jpg", "s3", P3, 6),
        _frame("f4.jpg", "s4", P4, 8),
    ]


def _assert_all_burst_frames(rows: dict, names: list[str], span_text: str) -> None:
    for name in names:
        row = rows[f"/a/{name}"]
        assert row.action == "", name
        assert row.match_confidence == "high", name
        assert row.reason.startswith(BURST_PREFIX), name
        assert span_text in row.reason, name


def test_chain_over_2s_becomes_one_stack():
    # Master: f0 is the keeper ("" unique), f1..f4 are REVIEW_DUPLICATE.
    # This pass: all five relabelled to "" / high under one stack.
    rows = _rows(classify(_five_frames(), source_priority=PRIORITY))
    assert len({r.group_id for r in rows.values()}) == 1
    _assert_all_burst_frames(rows, ["f0.jpg", "f1.jpg", "f2.jpg", "f3.jpg", "f4.jpg"],
                             "5 frames over 8 s")


def test_frame_first_paired_low_still_joins_stack():
    # Master: f2 (t0+4) is paired first with f1 (t0, 4 s away) -> REVIEW_DUPLICATE/low.
    # This pass: f2 is still a frame (it joins through high edges to f3) -> ""/high.
    records = [
        _frame("f1.jpg", "s1", P0, 0),
        _frame("f3.jpg", "s3", P1, 2),
        _frame("f2.jpg", "s2", P2, 4),
    ]
    rows = _rows(classify(records, source_priority=PRIORITY))
    _assert_all_burst_frames(rows, ["f1.jpg", "f2.jpg", "f3.jpg"], "3 frames over 4 s")


def test_byte_copy_inside_burst_stays_exact():
    # Master: the byte copy is EXACT/high. This pass: it keeps EXACT/high and its
    # reason; the other frames become "" / high under the stack.
    records = _five_frames() + [_frame("f2_copy.jpg", "s2", P2, 4)]
    rows = _rows(classify(records, source_priority=PRIORITY))
    exact = [r for r in rows.values() if r.action == "EXACT"]
    assert [Path(r.source_path).as_posix() for r in exact] == ["/a/f2_copy.jpg"]
    copy = exact[0]
    assert copy.match_confidence == "high"
    assert copy.reason.startswith("exact duplicate of ")
    assert BURST_PREFIX not in copy.reason
    _assert_all_burst_frames(rows, ["f0.jpg", "f1.jpg", "f2.jpg", "f3.jpg", "f4.jpg"],
                             "5 frames over 8 s")


def test_identical_phash_distinct_frame_joins_stack():
    # f0b has f0's pHash and a different sha at +1 s: master files it as a
    # format duplicate EXACT/high, linked to f0 by duplicate_of (high). Format
    # duplicates are not pHash comparators, so f0 must reach the chain through
    # an unclassified pair (f0-f1, 2 s, high). In the chain (span 4 s) f0b
    # becomes "" / high too.
    records = [
        _frame("f0.jpg", "s0", P0, 0),
        _frame("f0b.jpg", "s0b", P0, 1),
        _frame("f1.jpg", "s1", P1, 2),
        _frame("f2.jpg", "s2", P2, 4),
    ]
    rows = _rows(classify(records, source_priority=PRIORITY))
    _assert_all_burst_frames(rows, ["f0.jpg", "f0b.jpg", "f1.jpg", "f2.jpg"],
                             "4 frames over 4 s")


def test_group_within_2s_is_untouched():
    # Span exactly BURST_MAX_GAP_S (2 s) is not a burst: master's verdicts stay.
    records = [
        _frame("f0.jpg", "s0", P0, 0),
        _frame("f1.jpg", "s1", P1, 1),
        _frame("f2.jpg", "s2", P2, 2),
    ]
    rows = _rows(classify(records, source_priority=PRIORITY))
    assert len({r.group_id for r in rows.values()}) == 1
    assert not any(BURST_PREFIX in r.reason for r in rows.values())
    assert any(r.action == "REVIEW_DUPLICATE" for r in rows.values())


def test_undated_bridge_is_not_a_burst():
    # Two 3-frame moments 8 s apart, bridged by an undated file. The undated node
    # has no shot date, so the sorted dated gap is 8 s: not a burst.
    left = [_frame(f"l{i}.jpg", f"l{i}", ph, i) for i, ph in enumerate([P0, P1, P2])]
    bridge = _hr("/a/m_undated.jpg", "mid", "00000007fffffffc", None)
    right = [_frame(f"r{i}.jpg", f"r{i}", ph, 10 + i)
             for i, ph in enumerate(["00000007fffffff8", "0000000ffffffff0", "0000001fffffffe0"])]
    rows = _rows(classify(left + [bridge] + right, source_priority=PRIORITY))
    # GROUPING 14: the undated row is a content link to both sides, so the seven
    # files are one group. The stack test runs per run of consecutive frames
    # within 2 s: each side spans exactly 2 s (not longer), so no burst forms.
    ids = {rows[f"/a/l{i}.jpg"].group_id for i in range(3)} | {rows[f"/a/r{i}.jpg"].group_id for i in range(3)}
    assert len(ids) == 1 and rows["/a/m_undated.jpg"].group_id in ids
    assert not any(BURST_PREFIX in r.reason for r in rows.values())


def test_far_near_dup_joins_the_group_but_not_the_stack():
    # GROUPING 14: a near-duplicate 5 h 17 min away is content evidence, so it
    # joins the group as a REVIEW row; the stack is the run of frames within
    # 2 s of each other, so the far member is not a frame and keeps its verdict.
    records = _five_frames() + [_hr("/a/zz_leaf.jpg", "leaf", "00000000fffffffe",
                                    _at(5 * 3600 + 17 * 60), "src_a")]
    rows = _rows(classify(records, source_priority=PRIORITY))
    leaf = rows["/a/zz_leaf.jpg"]
    assert leaf.action == "REVIEW_DUPLICATE"
    assert leaf.group_id == rows["/a/f0.jpg"].group_id
    assert leaf.match_confidence == "high"
    assert not leaf.reason.startswith(BURST_PREFIX)
    _assert_all_burst_frames(rows, ["f0.jpg", "f1.jpg", "f2.jpg", "f3.jpg", "f4.jpg"],
                             "5 frames over 8 s")


def test_live_photo_video_ignored_by_detection():
    # The MOV peer has no pHash and a UTC date 8 h later; it must not open a gap
    # or change, and it keeps its own undecided verdict.
    heic = _five_frames()
    mov = HashResult(
        record=FileRecord(path=Path("/a/IMG_0000.mov"), source_label="src_a",
                          file_type="mov", pair_cluster=(Path("/a/f0.jpg"),)),
        sha256="mov", phash=None, dhash=None, mean_color=None,
        exif_date=_at(8 * 3600), pixel_width=4000, pixel_height=3000,
    )
    rows = _rows(classify(heic + [mov], source_priority=PRIORITY))
    _assert_all_burst_frames(rows, ["f0.jpg", "f1.jpg", "f2.jpg", "f3.jpg", "f4.jpg"],
                             "5 frames over 8 s")
    video = rows["/a/IMG_0000.mov"]
    assert video.action == ""
    assert video.reason == "unique"
    assert video.match_confidence is None


def test_burst_is_record_order_independent():
    records = _five_frames() + [_frame("f2_copy.jpg", "s2", P2, 4)]
    baseline = None
    for seed in range(20):
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        rows = _rows(classify(shuffled, source_priority=PRIORITY))
        view = {p: (r.action, r.reason, r.match_confidence, r.group_id)
                for p, r in rows.items()}
        if baseline is None:
            baseline = view
        assert view == baseline, f"seed {seed}"


def test_group_id_unchanged_by_burst_pass():
    # group_id is the lex-min path of the component, as before this pass.
    rows = _rows(classify(_five_frames(), source_priority=PRIORITY))
    group = list(rows.values())
    lex_min = min(r.source_path for r in group)
    assert all(r.group_id == lex_min for r in group)
    assert Path(lex_min).as_posix() == "/a/f0.jpg"
