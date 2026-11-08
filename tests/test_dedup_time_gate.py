"""GROUPING 14 — shot time is not grouping evidence; content decides.

GROUPING 10 to 13 demoted a pHash match whose EXIF shot times disagreed (more
than 2 s apart and not a whole number of half-hours) to a hint. The owner ruled
on 2026-11-09 that a grouping rule is about content, so that gate is gone: a
pixel-identical pair is ``EXACT`` whatever its dates say, a pair that hashes
alike with differing pixels is a ``REVIEW_DUPLICATE`` in one group, and the only
demotion left on this path is the PNG-pair (B7) rule.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from core.services.auto_select import non_keepers_for_aggressive_delete
from scanner.dedup import HashResult, _screenshot_pair, classify
from scanner.grey_store import GreyBytes, GreySig
from scanner.walker import FileRecord

# Non-degenerate pHash values (32 set bits, well inside the #516 entropy guard).
# NEAR differs from BASE in exactly 4 bits, so it is a pHash near-dup (hamming 4).
BASE = "00000000ffffffff"
NEAR = "00000000fffffff0"

T1 = datetime(2024, 6, 12, 20, 38, 0)   # the torii gate, first capture
T_EIGHT_H = datetime(2024, 6, 13, 4, 38, 0)   # exactly 8 h after T1
T_NEXT_MORNING = datetime(2024, 6, 13, 10, 24, 0)  # 13 h 46 min after T1
OTHER_PICTURE = GreyBytes(bytes([9]) * 16384)  # a different picture's grey signature


def _hr(
    path: str,
    sha256: str,
    phash: str | None = BASE,
    dhash: str | None = BASE,
    exif_date: datetime | None = None,
    source_label: str = "jdrive",
    file_type: str = "jpeg",
    grey128: GreySig | None = GreyBytes(bytes(16384)),
) -> HashResult:
    return HashResult(
        record=FileRecord(path=Path(path), source_label=source_label, file_type=file_type),
        sha256=sha256,
        phash=phash,
        dhash=dhash,
        mean_color=None,
        exif_date=exif_date,
        pixel_width=4000,
        pixel_height=3000,
        rgb32=bytes(3072),  # constant signature: format dups read as same pixels
        grey128=grey128,  # constant grey by default: the structure gate passes
    )


def _rows(result: list) -> dict:
    return {Path(r.source_path).as_posix(): r for r in result}


PRIORITY = {"src_a": 0, "src_b": 1}


class TestFormatDuplicateIgnoresShotTime:
    """pHash hamming 0, both lossy — the all-lossy EXACT path."""

    def test_identical_pixels_hours_apart_are_exact_high(self):
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=T_NEXT_MORNING, source_label="src_b")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        row = rows["/b/torii_1024.jpg"]
        assert row.action == "EXACT"
        assert row.match_confidence == "high"
        assert row.group_id == rows["/a/torii_0638.jpg"].group_id is not None
        assert "apart" not in row.reason

    def test_differing_pixels_hours_apart_are_review_in_one_group(self):
        """A re-shot of the same scene: same pHash, dHash agreeing, pixels
        differ. Content says near-duplicate, so REVIEW in one group; the 13 h
        gap is not read."""
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=T_NEXT_MORNING,
                  source_label="src_b", grey128=OTHER_PICTURE)
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        row = rows["/b/torii_1024.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.group_id == rows["/a/torii_0638.jpg"].group_id is not None
        assert "pixels differ" in row.reason

    def test_exactly_eight_hours_apart_stays_exact_high(self):
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=T_EIGHT_H, source_label="src_b")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        assert rows["/b/torii_1024.jpg"].action == "EXACT"
        assert rows["/b/torii_1024.jpg"].match_confidence == "high"

    def test_missing_exif_on_one_side_stays_exact(self):
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=None, source_label="src_b")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        assert rows["/b/torii_1024.jpg"].action == "EXACT"

    def test_two_pixel_identical_pngs_are_exact(self):
        # GROUPING 12: identical pixels are identity evidence even with B7 on.
        keeper = _hr("/a/shot_1.png", "p1", exif_date=T1, source_label="src_a", file_type="png")
        dup = _hr("/b/shot_2.png", "p2", exif_date=T_NEXT_MORNING, source_label="src_b",
                  file_type="png")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        row = rows["/b/shot_2.png"]
        assert row.action == "EXACT" and row.group_id is not None
        assert row.match_confidence == "high"
        assert "png vs png" in row.reason

    def test_two_differing_pngs_are_a_hint_under_b7(self):
        keeper = _hr("/a/shot_1.png", "p1", exif_date=T1, source_label="src_a", file_type="png")
        dup = _hr("/b/shot_2.png", "p2", exif_date=T1, source_label="src_b",
                  file_type="png", grey128=OTHER_PICTURE)
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        row = rows["/b/shot_2.png"]
        assert row.action == "" and row.group_id is None
        assert row.match_confidence == "low"
        assert "both screenshots" in row.reason


class TestNearDuplicateIgnoresShotTime:
    """pHash hamming 1..threshold with dHash agreeing — the near-dup path."""

    def test_near_dup_two_days_apart_is_review_high(self):
        a = _hr("/a/x.jpg", "n1", phash=BASE, dhash=BASE, exif_date=T1, source_label="src_a")
        b = _hr("/b/y.jpg", "n2", phash=NEAR, dhash=BASE,
                exif_date=T1 + timedelta(days=2), source_label="src_b")
        rows = _rows(classify([a, b], source_priority=PRIORITY))
        row = rows["/b/y.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert row.group_id == rows["/a/x.jpg"].group_id is not None
        assert "apart" not in row.reason, row.reason

    def test_near_dup_two_seconds_apart_is_the_same_verdict(self):
        a = _hr("/a/x.jpg", "n1", phash=BASE, dhash=BASE, exif_date=T1, source_label="src_a")
        b = _hr("/b/y.jpg", "n2", phash=NEAR, dhash=BASE,
                exif_date=T1 + timedelta(seconds=2), source_label="src_b")
        rows = _rows(classify([a, b], source_priority=PRIORITY))
        row = rows["/b/y.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert "; " not in row.reason

    def test_two_png_near_dups_demoted_to_low_hint(self):
        a = _hr("/a/x.png", "n1", phash=BASE, dhash=BASE, exif_date=T1,
                source_label="src_a", file_type="png")
        b = _hr("/b/y.png", "n2", phash=NEAR, dhash=BASE, exif_date=T1,
                source_label="src_b", file_type="png")
        rows = _rows(classify([a, b], source_priority=PRIORITY))
        row = rows["/b/y.png"]
        assert row.match_confidence == "low"
        assert row.group_id is None
        assert "both screenshots" in row.reason, row.reason


class TestScreenshotPair:
    def test_screenshot_pair_requires_both_png(self):
        png = _hr("/a/1.png", "v1", file_type="png")
        jpg = _hr("/a/2.jpg", "v2", file_type="jpeg")
        assert _screenshot_pair(png, png) is True
        assert _screenshot_pair(png, jpg) is False


class TestAggressiveDeleteExcludesLowRows:
    """Composes classify() output into the real aggressive-delete selector: a
    low (hint) row is never tagged while an EXACT copy is."""

    def test_hint_row_not_tagged_while_exact_copy_is(self):
        keeper_path = str(Path("/a/keep.png"))
        keeper = _hr("/a/keep.png", "g0", exif_date=T1, source_label="src_a", file_type="png")
        copy = _hr("/b/copy.png", "g1", exif_date=T_NEXT_MORNING, source_label="src_b",
                   file_type="png")
        other = _hr("/b/other.png", "g2", exif_date=T1, source_label="src_b",
                    file_type="png", grey128=OTHER_PICTURE)
        rows = classify([keeper, copy, other], source_priority=PRIORITY)
        for r in rows:
            r.score = 0.5  # ranked peers: aggressive selector requires a score
        by_path = {r.source_path: r for r in rows}
        assert by_path[str(Path("/b/copy.png"))].action == "EXACT"
        assert by_path[str(Path("/b/other.png"))].match_confidence == "low"

        tagged = non_keepers_for_aggressive_delete(rows, {keeper_path})

        assert str(Path("/b/copy.png")) in tagged
        assert str(Path("/b/other.png")) not in tagged
