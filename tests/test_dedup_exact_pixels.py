"""GROUPING_STRATEGY_VERSION "8" — EXACT means pixel-identical, not just pHash 0.

pHash is a greyscale DCT, so a colour-graded edit, a resized copy, or the next
frame of a burst can hash to 0 with different bytes. A pHash-0 format duplicate
keeps EXACT only when its 32x32 RGB signature matches the keeper's (same
dimensions, mean absolute difference <= FORMAT_DUP_MAX_RGB_DIFF). Otherwise it
is REVIEW_DUPLICATE at high confidence, in the same group.

Pixel signatures here are constant bytes: 0 everywhere (the keeper), and
shifted variants of it.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

from scanner.dedup import BURST_PREFIX, HashResult, classify
from scanner.walker import FileRecord

A = "00000000ffffffff"
T1 = datetime(2024, 6, 12, 20, 38, 0)
PRIORITY = {"src_a": 0}

SIG = bytes(3072)                              # keeper: all zeros
SIG_SHIFT_ONE_CHANNEL_20 = bytes(
    20 if i % 3 == 0 else 0 for i in range(3072)
)                                              # R +20: mean diff 6.67 > 6
SIG_ALL_SIX = bytes([6]) * 3072                # mean diff exactly 6.0
SIG_SIX_POINT_ONE = bytes(7 if i < 307 else 6 for i in range(3072))
# (3072*6 + 307) / 3072 = 6.0999 — just above 6.0; the nearest integer
# construction to 6.1 (6.1 * 3072 is not an integer).

PIXELS_DIFFER = "same pHash, pixels differ (edited, resized or another shot)"
PIXEL_CHECK_UNAVAILABLE = "same pHash, pixel check unavailable"


def _hr(
    path: str,
    sha256: str,
    phash: str = A,
    exif_date: datetime | None = T1,
    source_label: str = "src_a",
    file_type: str = "jpeg",
    rgb32: bytes | None = SIG,
    width: int = 4000,
    height: int = 3000,
) -> HashResult:
    return HashResult(
        record=FileRecord(path=Path(path), source_label=source_label, file_type=file_type),
        sha256=sha256,
        phash=phash,
        dhash=A,  # dHash agrees, so only the pixel gate decides
        mean_color=None,
        exif_date=exif_date,
        pixel_width=width,
        pixel_height=height,
        rgb32=rgb32,
    )


def _rows(result: list) -> dict:
    return {Path(r.source_path).as_posix(): r for r in result}


def _dup_pair(dup: HashResult) -> dict:
    """Keeper '/a/a_keep.jpg' (sorts first) plus the given duplicate."""
    keep = _hr("/a/a_keep.jpg", "k")
    return _rows(classify([keep, dup], source_priority=PRIORITY))


class TestPixelGate:
    def test_a_identical_pixels_same_dims_stays_exact(self):
        # master: EXACT (pHash 0 is enough). Now: identical rgb32 keeps EXACT.
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d"))
        row = rows["/a/b_dup.jpg"]
        assert row.action == "EXACT"
        assert row.reason.startswith("format duplicate of a_keep.jpg")

    def test_b_one_channel_shifted_is_review_high_same_group(self):
        # master: EXACT. Now: mean diff 6.67 > 6 -> REVIEW_DUPLICATE/high.
        # (A +10 shift on one channel is a mean diff of 3.33 and stays EXACT
        # under the mean-absolute gate; +20 is used so the test is on the gate.)
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_SHIFT_ONE_CHANNEL_20))
        dup = rows["/a/b_dup.jpg"]
        keep = rows["/a/a_keep.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert dup.match_confidence == "high"
        assert dup.reason == PIXELS_DIFFER
        assert dup.group_id is not None
        assert dup.group_id == keep.group_id

    def test_c_dimensions_differ_is_review_high(self):
        # master: EXACT. Now: dimensions differ -> REVIEW_DUPLICATE/high.
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d", width=3999))
        dup = rows["/a/b_dup.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert dup.match_confidence == "high"
        assert dup.reason == PIXELS_DIFFER

    def test_d_signature_missing_on_duplicate_is_review_high(self):
        # master: EXACT. Now: unverifiable identity -> REVIEW_DUPLICATE/high.
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=None))
        dup = rows["/a/b_dup.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert dup.match_confidence == "high"
        assert dup.reason == PIXEL_CHECK_UNAVAILABLE

    def test_d_signature_missing_on_keeper_is_review_high(self):
        # master: EXACT. Now: the keeper side is checked too.
        keep = _hr("/a/a_keep.jpg", "k", rgb32=None)
        dup = _hr("/a/b_dup.jpg", "d")
        rows = _rows(classify([keep, dup], source_priority=PRIORITY))
        row = rows["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.reason == PIXEL_CHECK_UNAVAILABLE

    def test_e_byte_copy_stays_exact_regardless_of_signature(self):
        # Same sha256: the byte-copy tier (_classify_exact) decides, not the
        # format-duplicate loop. master and now: EXACT, no pixel reason.
        keep = _hr("/a/a_keep.jpg", "same", rgb32=SIG)
        copy = _hr("/a/b_dup.jpg", "same", rgb32=SIG_SHIFT_ONE_CHANNEL_20)
        rows = _rows(classify([keep, copy], source_priority=PRIORITY))
        row = rows["/a/b_dup.jpg"]
        assert row.action == "EXACT"
        assert "pixel" not in row.reason

    def test_f_format_duplicate_in_burst_stack_still_ends_undecided(self):
        # Three frames, same pHash, identical pixels. The keeper (a_f0, path
        # sort) sits mid-burst at T+2 s so both others agree with it on shot
        # time (<= 2 s) and the span (4 s) exceeds the gap: a burst. Each
        # non-keeper is EXACT under the pixel gate, then the burst pass (#1042)
        # makes it "". master: the same final outcome (EXACT then relabelled).
        frames = [
            _hr("/a/a_f0.jpg", "f0", exif_date=T1 + timedelta(seconds=2)),
            _hr("/a/b_f1.jpg", "f1", exif_date=T1),
            _hr("/a/c_f2.jpg", "f2", exif_date=T1 + timedelta(seconds=4)),
        ]
        rows = _rows(classify(frames, source_priority=PRIORITY))
        for path in ("/a/b_f1.jpg", "/a/c_f2.jpg"):
            assert rows[path].action == ""
            assert rows[path].reason.startswith(BURST_PREFIX)
            assert "3 frames over 4 s" in rows[path].reason

    def test_g_mean_diff_exactly_six_stays_exact(self):
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_ALL_SIX))
        assert rows["/a/b_dup.jpg"].action == "EXACT"

    def test_g_mean_diff_just_above_six_demotes(self):
        # master: EXACT. Now: 6.0999 > 6.0 -> REVIEW_DUPLICATE/high.
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_SIX_POINT_ONE))
        dup = rows["/a/b_dup.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert dup.reason == PIXELS_DIFFER

    def test_h_verdict_is_independent_of_record_order(self):
        # master: EXACT in every order (no pixel check). Now: the same
        # REVIEW verdict for the shifted duplicate in every shuffle.
        base = [
            _hr("/a/a_keep.jpg", "k"),
            _hr("/a/b_dup.jpg", "d", rgb32=SIG_SHIFT_ONE_CHANNEL_20),
            _hr("/a/c_same.jpg", "c"),
        ]
        verdicts = set()
        for seed in range(6):
            records = list(base)
            random.Random(seed).shuffle(records)
            rows = _rows(classify(records, source_priority=PRIORITY))
            dup = rows["/a/b_dup.jpg"]
            verdicts.add((dup.action, dup.match_confidence, dup.reason))
        assert verdicts == {("REVIEW_DUPLICATE", "high", PIXELS_DIFFER)}
