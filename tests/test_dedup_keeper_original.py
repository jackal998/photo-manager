"""GROUPING_STRATEGY_VERSION "19" — the keeper is the member the evidence names
an original, before the format order.

A copy made from a picture normally has no more pixels and no more EXIF than
its source. So in an all-lossy pHash bucket a member with a capture date and
more pixels than every undated member is the keeper, whatever its format or
source priority. Without that evidence (all dated, all undated, an undated
member at least as large, an undated member of unknown size) the GROUPING 17
order stands: format, source priority, pixel count, path.
"""

from __future__ import annotations

from pathlib import Path

from scanner.dedup import classify
from tests.test_dedup_exact_pixels import PIXELS_DIFFER, T1, _hr, _rows

PRIORITY = {"src_a": 0}
PRIORITY_TWO_SOURCES = {"src_a": 0, "src_b": 1}


def _duplicate_of(row) -> str | None:
    return None if row.duplicate_of is None else Path(row.duplicate_of).as_posix()


def _png(path: str, sha: str, **kwargs):
    return _hr(path, sha, file_type="png", **kwargs)


class TestEvidenceOriginalIsKeeper:
    def test_a_larger_dated_png_keeps_over_a_smaller_undated_jpeg(self):
        # The measured case: a screenshot PNG and the smaller JPEG a chat app
        # made of it. Before: JPEG ranks above PNG, so the PNG was the duplicate.
        png = _png("/a/b_shot.png", "p", exif_date=T1, width=1776, height=2400)
        jpeg = _hr("/a/a_sent.jpg", "j", exif_date=None, width=1100, height=1487)
        for records in ([png, jpeg], [jpeg, png]):
            rows = _rows(classify(records, source_priority=PRIORITY))
            dup = rows["/a/a_sent.jpg"]
            assert dup.action == "REVIEW_DUPLICATE"
            assert dup.reason == PIXELS_DIFFER
            assert _duplicate_of(dup) == "/a/b_shot.png"
            assert rows["/a/b_shot.png"].duplicate_of is None

    def test_b_evidence_beats_source_priority(self):
        png = _png("/b/b_shot.png", "p", source_label="src_b", width=4000, height=3000)
        jpeg = _hr("/a/a_sent.jpg", "j", exif_date=None, width=2000, height=1500)
        rows = _rows(classify([jpeg, png], source_priority=PRIORITY_TWO_SOURCES))
        assert _duplicate_of(rows["/a/a_sent.jpg"]) == "/b/b_shot.png"
        assert rows["/b/b_shot.png"].duplicate_of is None

    def test_c_two_evidence_originals_fall_back_to_the_format_order(self):
        # Both dated members exceed the undated one, so the format decides
        # between them and the undated copy is judged against that keeper.
        png = _png("/a/a_big.png", "p", width=4000, height=3000)
        jpeg = _hr("/a/b_mid.jpg", "m", width=3000, height=2250)
        small = _hr("/a/c_small.jpg", "s", exif_date=None, width=2000, height=1500)
        rows = _rows(classify([png, jpeg, small], source_priority=PRIORITY))
        assert rows["/a/b_mid.jpg"].duplicate_of is None
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/b_mid.jpg"
        assert _duplicate_of(rows["/a/c_small.jpg"]) == "/a/b_mid.jpg"


class TestNoEvidenceKeepsTheFormatOrder:
    def _keeper_is_the_jpeg(self, png, jpeg) -> None:
        rows = _rows(classify([png, jpeg], source_priority=PRIORITY))
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/b_small.jpg"
        assert rows["/a/b_small.jpg"].duplicate_of is None

    def test_d_both_dated(self):
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", width=2000, height=1500),
        )

    def test_e_both_undated(self):
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", exif_date=None, width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", exif_date=None, width=2000, height=1500),
        )

    def test_f_the_larger_member_is_the_undated_one(self):
        # More pixels on one side, the capture date on the other: no member
        # leads on both, so nothing names an original.
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", exif_date=None, width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", width=2000, height=1500),
        )

    def test_g_a_dated_png_of_the_same_size_does_not_lead(self):
        # "More pixels" is strict: a dated copy of the same size as an undated
        # one is not promoted (a 90-degree swap keeps the pixel count).
        png = _png("/a/a_big.png", "p", width=4000, height=3000)
        jpeg = _hr("/a/b_small.jpg", "j", exif_date=None, width=3000, height=4000)
        rows = _rows(classify([png, jpeg], source_priority=PRIORITY))
        assert rows["/a/a_big.png"].duplicate_of is not None
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/b_small.jpg"
        assert rows["/a/b_small.jpg"].duplicate_of is None

    def test_h_an_undated_member_of_unknown_size_blocks_the_evidence(self):
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", exif_date=None, width=None, height=None),
        )

    def test_i_a_larger_undated_member_blocks_the_evidence_for_the_whole_bucket(self):
        # The dated PNG exceeds one undated JPEG but not the other, so it is
        # not the source of every undated member and the format order stands.
        png = _png("/a/a_big.png", "p", width=4000, height=3000)
        small = _hr("/a/b_small.jpg", "s", exif_date=None, width=2000, height=1500)
        huge = _hr("/a/c_huge.jpg", "h", exif_date=None, width=6000, height=4500)
        rows = _rows(classify([png, small, huge], source_priority=PRIORITY))
        assert rows["/a/c_huge.jpg"].duplicate_of is None
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/c_huge.jpg"
        assert _duplicate_of(rows["/a/b_small.jpg"]) == "/a/c_huge.jpg"
