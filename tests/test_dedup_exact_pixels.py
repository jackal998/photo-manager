"""GROUPING_STRATEGY_VERSION "8" — EXACT means pixel-identical, not just pHash 0.

pHash is a greyscale DCT, so a colour-graded edit, a resized copy, or the next
frame of a burst can hash to 0 with different bytes. A pHash-0 format duplicate
keeps EXACT only when its 32x32 RGB signature matches the keeper's:

  1. same pixel dimensions (or a 90-degree swap, see test_rotated_dims_*),
  2. mean absolute byte difference <= FORMAT_DUP_MAX_RGB_DIFF (6.0),
  3. max per-channel mean shift <= FORMAT_DUP_MAX_CHANNEL_SHIFT (4.0).

Otherwise it is REVIEW_DUPLICATE in the same group, at the dHash-vote confidence.

Signatures are constant-valued bytes around a keeper of 100 per byte, so each
test's expected mean difference and channel shift can be computed by hand.
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
N = 3072  # 32 * 32 * 3 bytes; channel c is bytes c, c+3, ... (1,024 pixels)

KEEPER = bytes([100]) * N  # the keeper's signature (the default for every row)


def _sig(fn) -> bytes:
    return bytes(fn(i) for i in range(N))


# Max per-channel shift 20, mean diff 6.67: fails the mean-diff gate and the shift gate.
SIG_SHIFT_ONE_CHANNEL_20 = _sig(lambda i: 120 if i % 3 == 0 else 100)
# +10 on R on every pixel: mean diff 3.33 (passes gate 2), shift 10 (fails gate 3).
SIG_ONE_CHANNEL_PLUS_10 = _sig(lambda i: 110 if i % 3 == 0 else 100)
# Alternating +5 / -7 per pixel: mean diff 5.5, every channel's mean shift 2.0.
SIG_MAD_5_5_SHIFT_2 = _sig(lambda i: 105 if (i // 3) % 4 != 3 else 93)
# Alternating +8 / -4: mean diff exactly 6.0, every channel's mean shift 2.0.
SIG_MAD_6_SHIFT_2 = _sig(lambda i: 108 if (i // 3) % 2 == 0 else 96)
# SIG_MAD_6_SHIFT_2 with 307 of the +8 bytes raised to +9: mean diff 6.0997.
_mad_over = [108 if (i // 3) % 2 == 0 else 96 for i in range(N)]
for _i in [i for i in range(N) if (i // 3) % 2 == 0][:307]:
    _mad_over[_i] = 109
SIG_MAD_6_POINT_1 = bytes(_mad_over)
# R +4 on every pixel: channel shift exactly 4.0 (mean diff 1.33).
SIG_SHIFT_4_0 = _sig(lambda i: 104 if i % 3 == 0 else 100)
# R +4 on 922 pixels and +5 on 102: channel shift 4.0996 (mean diff 1.37).
SIG_SHIFT_4_1 = _sig(
    lambda i: (105 if (i // 3) < 102 else 104) if i % 3 == 0 else 100
)

PIXELS_DIFFER = "same pHash, pixels differ (edited, resized or another shot)"
PIXEL_CHECK_UNAVAILABLE = "same pHash, pixel check unavailable"


def _hr(
    path: str,
    sha256: str,
    phash: str = A,
    exif_date: datetime | None = T1,
    source_label: str = "src_a",
    file_type: str = "jpeg",
    rgb32: bytes | None = KEEPER,
    width: int = 4000,
    height: int = 3000,
    dhash: str | None = A,
) -> HashResult:
    return HashResult(
        record=FileRecord(path=Path(path), source_label=source_label, file_type=file_type),
        sha256=sha256,
        phash=phash,
        dhash=dhash,
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
        row = _dup_pair(_hr("/a/b_dup.jpg", "d"))["/a/b_dup.jpg"]
        assert row.action == "EXACT"
        assert row.reason.startswith("format duplicate of a_keep.jpg")

    def test_b_one_channel_shifted_is_review_high_same_group(self):
        # master: EXACT. Now: mean diff 6.67 > 6 -> REVIEW_DUPLICATE/high.
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_SHIFT_ONE_CHANNEL_20))
        dup = rows["/a/b_dup.jpg"]
        keep = rows["/a/a_keep.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert dup.match_confidence == "high"
        assert dup.reason == PIXELS_DIFFER
        assert dup.group_id is not None
        assert dup.group_id == keep.group_id

    def test_b_one_channel_plus_10_is_review_by_channel_shift(self):
        # master: EXACT. Now: mean diff 3.33 passes gate 1, but the R-channel
        # shift of 10 fails the channel-shift gate (a single-channel edit).
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_ONE_CHANNEL_PLUS_10))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.reason == PIXELS_DIFFER

    def test_b_mad_5_5_with_shift_2_stays_exact(self):
        # master: EXACT. Now: mean diff 5.5 <= 6 and channel shift 2.0 <= 4 -> EXACT.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_MAD_5_5_SHIFT_2))["/a/b_dup.jpg"]
        assert row.action == "EXACT"

    def test_c_dimensions_differ_is_review_high(self):
        # master: EXACT. Now: 3999 wide is not a rotation -> REVIEW_DUPLICATE/high.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", width=3999))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.reason == PIXELS_DIFFER

    def test_c_rotated_dims_same_pixels_stays_exact(self):
        # The EXIF-oriented original and its rotation-baked export: pixel_width/
        # height are read before orientation (4000x3000 vs 3000x4000), while the
        # signatures are taken after it and are identical. master: EXACT.
        # Now: a 90-degree swap of the dimensions still passes.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", width=3000, height=4000))["/a/b_dup.jpg"]
        assert row.action == "EXACT"

    def test_c_rotated_dims_different_pixels_is_review(self):
        # The swap is tolerated for dimensions only; a different image still fails.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", width=3000, height=4000,
                            rgb32=SIG_SHIFT_ONE_CHANNEL_20))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.reason == PIXELS_DIFFER

    def test_d_signature_missing_on_duplicate_is_review_high(self):
        # master: EXACT. Now: unverifiable identity -> REVIEW_DUPLICATE/high.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=None))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.reason == PIXEL_CHECK_UNAVAILABLE

    def test_d_signature_missing_on_keeper_is_review_high(self):
        # master: EXACT. Now: the keeper side is checked too.
        keep = _hr("/a/a_keep.jpg", "k", rgb32=None)
        dup = _hr("/a/b_dup.jpg", "d")
        row = _rows(classify([keep, dup], source_priority=PRIORITY))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.reason == PIXEL_CHECK_UNAVAILABLE

    def test_e_byte_copy_stays_exact_regardless_of_signature(self):
        # Same sha256: the byte-copy tier decides, not the format-duplicate loop.
        # master and now: EXACT, no pixel reason.
        keep = _hr("/a/a_keep.jpg", "same", rgb32=KEEPER)
        copy = _hr("/a/b_dup.jpg", "same", rgb32=SIG_SHIFT_ONE_CHANNEL_20)
        row = _rows(classify([keep, copy], source_priority=PRIORITY))["/a/b_dup.jpg"]
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
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_MAD_6_SHIFT_2))["/a/b_dup.jpg"]
        assert row.action == "EXACT"

    def test_g_mean_diff_just_above_six_demotes(self):
        # master: EXACT. Now: 6.0997 > 6.0 -> REVIEW_DUPLICATE/high.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_MAD_6_POINT_1))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.reason == PIXELS_DIFFER

    def test_g_channel_shift_exactly_four_stays_exact(self):
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_SHIFT_4_0))["/a/b_dup.jpg"]
        assert row.action == "EXACT"

    def test_g_channel_shift_just_above_four_demotes(self):
        # master: EXACT. Now: channel shift 4.0996 > 4.0 -> REVIEW_DUPLICATE/high.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_SHIFT_4_1))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.reason == PIXELS_DIFFER

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

    def test_i_dhash_unknown_keeps_review_low(self):
        # master: an EXACT row at low confidence (dHash unknown) that makes a
        # hint edge. Now: the pixel-differ REVIEW row keeps that low confidence,
        # so it is still a hint and not a certain high edge.
        keep = _hr("/a/a_keep.jpg", "k", dhash=None)
        dup = _hr("/a/b_dup.jpg", "d", dhash=None, rgb32=SIG_SHIFT_ONE_CHANNEL_20)
        row = _rows(classify([keep, dup], source_priority=PRIORITY))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "low"
        assert row.reason == PIXELS_DIFFER
