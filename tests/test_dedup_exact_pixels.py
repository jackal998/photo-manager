"""GROUPING_STRATEGY_VERSION "11" — EXACT means pixel-identical, not just pHash 0.

pHash is a greyscale DCT, so a colour-graded edit, a resized copy, or the next
frame of a burst can hash to 0 with different bytes. A pHash-0 format duplicate
keeps EXACT only when its signatures match the keeper's:

  1. same pixel dimensions (or a 90-degree swap, see test_c_rotated_*),
  2. max per-channel mean shift of the 32x32 RGB signatures <= 4.0
     (FORMAT_DUP_MAX_CHANNEL_SHIFT),
  3. on the 128x128 grey signatures, no 4x4 block differs by more than 4.0 on
     average and at most 16 pixels differ by more than 8 (_grey_identical).

Otherwise it is REVIEW_DUPLICATE in the same group, at the dHash-vote confidence.

RGB signatures are constant-valued bytes around a keeper of 100 per byte. Grey
signatures are a flat 100 with the patch each test names, so each test's
expected block means and tail counts can be computed by hand.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

from scanner.dedup import BURST_PREFIX, HashResult, classify
from scanner.grey_store import GREY_SIDE, GREY_SIG_BYTES, GreyBytes, GreySig
from scanner.walker import FileRecord

A = "00000000ffffffff"
T1 = datetime(2024, 6, 12, 20, 38, 0)
PRIORITY = {"src_a": 0}
N = 3072  # 32 * 32 * 3 bytes; channel c is bytes c, c+3, ... (1,024 pixels)

KEEPER = bytes([100]) * N  # the keeper's RGB signature (the default for every row)
GREY_KEEPER = GreyBytes(bytes([100]) * GREY_SIG_BYTES)  # the keeper's grey (default)


def _sig(fn) -> bytes:
    return bytes(fn(i) for i in range(N))


def _grey(delta_at) -> GreyBytes:
    """A grey signature: 100 everywhere, plus ``delta_at(x, y)`` per pixel."""
    return GreyBytes(bytes(
        100 + delta_at(x, y)
        for y in range(GREY_SIDE) for x in range(GREY_SIDE)
    ))


# Max per-channel shift 20: fails the channel-shift gate.
SIG_SHIFT_ONE_CHANNEL_20 = _sig(lambda i: 120 if i % 3 == 0 else 100)
# +10 on R on every pixel: channel shift 10 (fails the channel-shift gate).
SIG_ONE_CHANNEL_PLUS_10 = _sig(lambda i: 110 if i % 3 == 0 else 100)
# Alternating +5 / -7 per pixel: every channel's mean shift 2.0.
SIG_MAD_5_5_SHIFT_2 = _sig(lambda i: 105 if (i // 3) % 4 != 3 else 93)
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
    grey128: GreySig | None = GREY_KEEPER,
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
        grey128=grey128,
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
        # master: EXACT. Now: channel shift 20 > 4 -> REVIEW_DUPLICATE/high.
        rows = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_SHIFT_ONE_CHANNEL_20))
        dup = rows["/a/b_dup.jpg"]
        keep = rows["/a/a_keep.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert dup.match_confidence == "high"
        assert dup.reason == PIXELS_DIFFER
        assert dup.group_id is not None
        assert dup.group_id == keep.group_id

    def test_b_one_channel_plus_10_is_review_by_channel_shift(self):
        # master: EXACT. Now: the R-channel shift of 10 fails the channel-shift
        # gate (a single-channel edit), though the grey signatures are equal.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", rgb32=SIG_ONE_CHANNEL_PLUS_10))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.reason == PIXELS_DIFFER

    def test_b_mad_5_5_with_shift_2_stays_exact(self):
        # master: EXACT. Now: channel shift 2.0 <= 4 and equal grey -> EXACT.
        # The mean byte difference (5.5) is no longer a gate, so it cannot demote.
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

    def test_c_rotated_dims_grey_patch_is_review(self):
        # The swap is tolerated for dimensions only; a grey structure change still fails.
        grey = _grey(lambda x, y: 60 if x < 12 and y < 12 else 0)
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", width=3000, height=4000,
                            grey128=grey))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.reason == PIXELS_DIFFER

    def test_grey_12x12_patch_at_plus_60_is_review_in_keepers_group(self):
        # Equal colour, a 12x12 structure change: 144 pixels over 8 demote it.
        keep = _hr("/a/a_keep.jpg", "k")
        grey = _grey(lambda x, y: 60 if x < 12 and y < 12 else 0)
        rows = _rows(classify([keep, _hr("/a/b_dup.jpg", "d", grey128=grey)],
                              source_priority=PRIORITY))
        row = rows["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.reason == PIXELS_DIFFER
        assert row.group_id is not None
        assert row.group_id == rows["/a/a_keep.jpg"].group_id

    def test_grey_noise_of_zero_or_one_stays_exact(self):
        # A re-encode: every grey pixel moves by 0 or +1, so the gate passes.
        row = _dup_pair(_hr("/a/b_dup.jpg", "d",
                            grey128=_grey(lambda x, y: (x + y) % 2)))["/a/b_dup.jpg"]
        assert row.action == "EXACT"

    def test_d_grey_signature_missing_on_duplicate_is_review_high(self):
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", grey128=None))["/a/b_dup.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.reason == PIXEL_CHECK_UNAVAILABLE

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

    def test_g_grey_block_mean_exactly_four_stays_exact(self):
        # One aligned 4x4 block at +4: block mean exactly 4.0 <= GREY_BLOCK_MAX.
        grey = _grey(lambda x, y: 4 if x < 4 and y < 4 else 0)
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", grey128=grey))["/a/b_dup.jpg"]
        assert row.action == "EXACT"

    def test_g_grey_block_mean_just_above_four_demotes(self):
        # The same block with one pixel at +5: mean 65 / 16 = 4.0625 > 4.0.
        grey = _grey(lambda x, y: 5 if (x, y) == (0, 0) else 4 if x < 4 and y < 4 else 0)
        row = _dup_pair(_hr("/a/b_dup.jpg", "d", grey128=grey))["/a/b_dup.jpg"]
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

    def test_i_dhash_unknown_is_a_hint_not_a_group(self):
        # GROUPING "10": a pixel-differ format duplicate with no dHash is low
        # confidence, so it is a hint. It writes no REVIEW row and groups nothing.
        keep = _hr("/a/a_keep.jpg", "k", dhash=None)
        dup = _hr("/a/b_dup.jpg", "d", dhash=None, rgb32=SIG_SHIFT_ONE_CHANNEL_20)
        rows = _rows(classify([keep, dup], source_priority=PRIORITY))
        row = rows["/a/b_dup.jpg"]
        assert row.action == ""
        assert row.group_id is None and rows["/a/a_keep.jpg"].group_id is None
        assert row.match_confidence == "low"
        assert "dHash unavailable" in row.reason, row.reason
