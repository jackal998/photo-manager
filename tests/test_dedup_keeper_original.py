"""GROUPING_STRATEGY_VERSION "19" — a member the evidence names a copy is not
the keeper, whatever the format order says.

A copy made from a picture normally has no more pixels and no more EXIF than
its source. So in an all-lossy pHash bucket a member with no capture date and
fewer pixels than a dated member ranks after every other member. Members the
evidence does not name a copy (every dated member, an undated member at least
as large as every dated one, an undated member of unknown size) keep the
GROUPING 17 order between them: format, source priority, pixel count, path.
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


class TestEvidenceCopyIsNotKeeper:
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

    def test_c_two_dated_members_fall_back_to_the_format_order(self):
        # Only the undated copy is ranked down; the format decides between the
        # two dated members, and the copy is judged against that keeper.
        png = _png("/a/a_big.png", "p", width=4000, height=3000)
        jpeg = _hr("/a/b_mid.jpg", "m", width=3000, height=2250)
        small = _hr("/a/c_small.jpg", "s", exif_date=None, width=2000, height=1500)
        rows = _rows(classify([png, jpeg, small], source_priority=PRIORITY))
        assert rows["/a/b_mid.jpg"].duplicate_of is None
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/b_mid.jpg"
        assert _duplicate_of(rows["/a/c_small.jpg"]) == "/a/b_mid.jpg"

    def test_d_a_stripped_copy_does_not_hand_the_keeper_to_a_larger_export(self):
        # A dated HEIC, its EXIF-stripped copy (same pixels) and a larger dated
        # export. The copy says nothing about which dated member is the
        # original, so the format order still keeps the HEIC.
        heic = _hr("/a/a_orig.heic", "h", file_type="heic", width=4032, height=3024)
        copy = _hr("/a/b_copy.jpg", "c", exif_date=None, width=4032, height=3024)
        export = _hr("/a/c_export.jpg", "e", width=6048, height=4536)
        for records in ([heic, copy, export], [export, copy, heic]):
            rows = _rows(classify(records, source_priority=PRIORITY))
            assert rows["/a/a_orig.heic"].duplicate_of is None
            assert rows["/a/b_copy.jpg"].action == "EXACT"
            assert _duplicate_of(rows["/a/b_copy.jpg"]) == "/a/a_orig.heic"
            assert _duplicate_of(rows["/a/c_export.jpg"]) == "/a/a_orig.heic"

    def test_e_a_ranked_down_copy_loses_even_with_the_best_source(self):
        # Without the rank the small JPEG of the preferred source would keep
        # (same format as the huge one, better source), although the dated PNG
        # names it a copy. The keeper is the best member not named a copy.
        png = _png("/b/a_big.png", "p", source_label="src_b", width=4000, height=3000)
        small = _hr("/a/b_small.jpg", "s", exif_date=None, width=2000, height=1500)
        huge = _hr(
            "/b/c_huge.jpg", "h", source_label="src_b", exif_date=None, width=6000, height=4500
        )
        rows = _rows(classify([png, small, huge], source_priority=PRIORITY_TWO_SOURCES))
        assert rows["/b/c_huge.jpg"].duplicate_of is None
        assert _duplicate_of(rows["/a/b_small.jpg"]) == "/b/c_huge.jpg"
        assert _duplicate_of(rows["/b/a_big.png"]) == "/b/c_huge.jpg"


class TestNoEvidenceKeepsTheFormatOrder:
    def _keeper_is_the_jpeg(self, png, jpeg) -> None:
        rows = _rows(classify([png, jpeg], source_priority=PRIORITY))
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/b_small.jpg"
        assert rows["/a/b_small.jpg"].duplicate_of is None

    def test_f_both_dated(self):
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", width=2000, height=1500),
        )

    def test_g_both_undated(self):
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", exif_date=None, width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", exif_date=None, width=2000, height=1500),
        )

    def test_h_the_larger_member_is_the_undated_one(self):
        # More pixels on one side, the capture date on the other: no member
        # leads on both, so nothing names a copy.
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", exif_date=None, width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", width=2000, height=1500),
        )

    def test_i_an_undated_member_of_the_same_size_is_not_ranked_down(self):
        # "Fewer pixels" is strict: an undated copy of the same size as the
        # dated one stays in the format order (a 90-degree swap keeps the
        # pixel count).
        png = _png("/a/a_big.png", "p", width=4000, height=3000)
        jpeg = _hr("/a/b_small.jpg", "j", exif_date=None, width=3000, height=4000)
        rows = _rows(classify([png, jpeg], source_priority=PRIORITY))
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/b_small.jpg"
        assert rows["/a/b_small.jpg"].duplicate_of is None

    def test_j_an_undated_member_of_unknown_size_is_not_ranked_down(self):
        self._keeper_is_the_jpeg(
            _png("/a/a_big.png", "p", width=4000, height=3000),
            _hr("/a/b_small.jpg", "j", exif_date=None, width=None, height=None),
        )

    def test_k_a_larger_undated_member_keeps_its_place_in_the_format_order(self):
        # The dated PNG names the small JPEG a copy but not the huge one, so
        # the format order decides between the PNG and the huge JPEG.
        png = _png("/a/a_big.png", "p", width=4000, height=3000)
        small = _hr("/a/b_small.jpg", "s", exif_date=None, width=2000, height=1500)
        huge = _hr("/a/c_huge.jpg", "h", exif_date=None, width=6000, height=4500)
        rows = _rows(classify([png, small, huge], source_priority=PRIORITY))
        assert rows["/a/c_huge.jpg"].duplicate_of is None
        assert _duplicate_of(rows["/a/a_big.png"]) == "/a/c_huge.jpg"
        assert _duplicate_of(rows["/a/b_small.jpg"]) == "/a/c_huge.jpg"
