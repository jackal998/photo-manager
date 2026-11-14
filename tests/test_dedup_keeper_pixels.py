"""GROUPING_STRATEGY_VERSION "17" — the keeper of a duplicate group prefers the
picture with more pixels before the path tiebreak.

The keeper of an all-lossy pHash bucket is ordered by format, then source
priority, then pixel count (larger first; a row with unknown dimensions after
every known one), then the lexicographically smallest path. A resized copy
hashes alike, so before this rule its path alone could make it the keeper and
show the full-size original as its duplicate. The near-duplicate route (pHash
distance above 0) orders its pair by source priority, then the same pixel
count, then path.
"""

from __future__ import annotations

from pathlib import Path

from scanner.dedup import classify
from tests.test_dedup_exact_pixels import PIXELS_DIFFER, SIG_SHIFT_ONE_CHANNEL_20, _hr, _rows

PRIORITY = {"src_a": 0}
PRIORITY_TWO_SOURCES = {"src_a": 0, "src_b": 1}
A = "00000000ffffffff"
A_ONE_BIT = "00000000fffffffe"  # pHash distance 1 from A


def _duplicate_of(row) -> str | None:
    return None if row.duplicate_of is None else Path(row.duplicate_of).as_posix()


class TestLargerPictureIsKeeper:
    def test_a_larger_picture_keeps_over_a_smaller_one_with_a_smaller_path(self):
        big = _hr("/a/b_big.jpg", "big", width=4000, height=3000)
        small = _hr("/a/a_small.jpg", "small", width=2000, height=1500)
        rows = _rows(classify([big, small], source_priority=PRIORITY))
        dup = rows["/a/a_small.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert dup.reason == PIXELS_DIFFER
        assert _duplicate_of(dup) == "/a/b_big.jpg"
        assert rows["/a/b_big.jpg"].duplicate_of is None

    def test_b_near_duplicate_pair_keeps_the_larger_picture(self):
        big = _hr("/a/b_big.jpg", "big", width=4000, height=3000)
        small = _hr("/a/a_small.jpg", "small", phash=A_ONE_BIT, width=2000, height=1500)
        rows = _rows(classify([big, small], source_priority=PRIORITY))
        dup = rows["/a/a_small.jpg"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert _duplicate_of(dup) == "/a/b_big.jpg"
        assert rows["/a/b_big.jpg"].duplicate_of is None

    def test_c_equal_pixel_count_still_keeps_the_smaller_path(self):
        # A 90-degree swap has the same pixel count and identical pixels: the
        # tie falls through to the path, whatever the input order.
        keep = _hr("/a/a_keep.jpg", "k", width=4000, height=3000)
        swapped = _hr("/a/b_swap.jpg", "s", width=3000, height=4000)
        for records in ([keep, swapped], [swapped, keep]):
            rows = _rows(classify(records, source_priority=PRIORITY))
            assert rows["/a/b_swap.jpg"].action == "EXACT"
            assert _duplicate_of(rows["/a/b_swap.jpg"]) == "/a/a_keep.jpg"
            assert rows["/a/a_keep.jpg"].duplicate_of is None

    def test_c_equal_pixel_count_with_pixels_differing_keeps_the_smaller_path(self):
        keep = _hr("/a/a_keep.jpg", "k", width=4000, height=3000)
        dup = _hr("/a/b_dup.jpg", "d", width=4000, height=3000, rgb32=SIG_SHIFT_ONE_CHANNEL_20)
        rows = _rows(classify([dup, keep], source_priority=PRIORITY))
        assert rows["/a/b_dup.jpg"].action == "REVIEW_DUPLICATE"
        assert _duplicate_of(rows["/a/b_dup.jpg"]) == "/a/a_keep.jpg"
        assert rows["/a/a_keep.jpg"].duplicate_of is None

    def test_d_unknown_dimensions_do_not_beat_known_dimensions(self):
        known = _hr("/a/b_known.jpg", "k", width=2000, height=1500)
        unknown = _hr("/a/a_unknown.jpg", "u", width=None, height=None)
        rows = _rows(classify([unknown, known], source_priority=PRIORITY))
        assert rows["/a/a_unknown.jpg"].action == "REVIEW_DUPLICATE"
        assert _duplicate_of(rows["/a/a_unknown.jpg"]) == "/a/b_known.jpg"
        assert rows["/a/b_known.jpg"].duplicate_of is None

    def test_e_higher_source_priority_beats_a_larger_picture(self):
        big = _hr("/a/a_big.jpg", "big", source_label="src_b", width=4000, height=3000)
        small = _hr("/a/b_small.jpg", "small", source_label="src_a", width=2000, height=1500)
        rows = _rows(classify([big, small], source_priority=PRIORITY_TWO_SOURCES))
        assert rows["/a/a_big.jpg"].action == "REVIEW_DUPLICATE"
        assert _duplicate_of(rows["/a/a_big.jpg"]) == "/a/b_small.jpg"
        assert rows["/a/b_small.jpg"].duplicate_of is None

    def test_f_format_priority_beats_a_larger_picture(self):
        jpeg = _hr("/a/a_big.jpg", "big", file_type="jpeg", width=4000, height=3000)
        heic = _hr("/a/b_small.heic", "heic", file_type="heic", width=2000, height=1500)
        rows = _rows(classify([jpeg, heic], source_priority=PRIORITY))
        assert rows["/a/a_big.jpg"].action == "REVIEW_DUPLICATE"
        assert _duplicate_of(rows["/a/a_big.jpg"]) == "/a/b_small.heic"
        assert rows["/a/b_small.heic"].duplicate_of is None
