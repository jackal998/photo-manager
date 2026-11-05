"""dedup-core research 2026-11-05 class B — shot-time and PNG-pair confidence gates.

A pHash match (exact or near) whose EXIF shot times disagree, or whose two
files are both PNG screenshots, is demoted to ``REVIEW_DUPLICATE`` with
``match_confidence="low"``. Edges (``duplicate_of``) are unchanged, so grouping
is unchanged — only the label and confidence move, and the aggressive
auto-select (``non_keepers_for_aggressive_delete``) never tags those rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from core.services.auto_select import non_keepers_for_aggressive_delete
from scanner.dedup import HashResult, _screenshot_pair, _shot_time_vote, classify
from scanner.walker import FileRecord

# Non-degenerate pHash values (32 set bits, well inside the #516 entropy guard).
# NEAR differs from BASE in exactly 4 bits, so it is a pHash near-dup (hamming 4).
BASE = "00000000ffffffff"
NEAR = "00000000fffffff0"

T1 = datetime(2024, 6, 12, 20, 38, 0)   # the torii gate, first capture
T_EIGHT_H = datetime(2024, 6, 13, 4, 38, 0)   # exactly 8 h after T1 (tz-offset gap)
T_NEXT_MORNING = datetime(2024, 6, 13, 10, 24, 0)  # 13 h 46 min after T1 → disagree


def _hr(
    path: str,
    sha256: str,
    phash: str | None = BASE,
    dhash: str | None = BASE,
    exif_date: datetime | None = None,
    source_label: str = "jdrive",
    file_type: str = "jpeg",
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
    )


def _rows(result: list) -> dict:
    return {Path(r.source_path).as_posix(): r for r in result}


PRIORITY = {"src_a": 0, "src_b": 1}


class TestFormatDuplicateTimeGate:
    """pHash hamming 0, both lossy — the all-lossy EXACT path."""

    def test_disagreeing_shot_times_demoted_to_review_low(self):
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=T_NEXT_MORNING, source_label="src_b")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        row = rows["/b/torii_1024.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "low"
        assert row.duplicate_of is not None
        assert "13.8 h apart" in row.reason
        assert rows["/a/torii_0638.jpg"].group_id == row.group_id
        assert all(r.action != "EXACT" for r in rows.values())

    def test_exactly_eight_hours_apart_stays_exact_high(self):
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=T_EIGHT_H, source_label="src_b")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        row = rows["/b/torii_1024.jpg"]
        assert row.action == "EXACT"
        assert row.match_confidence == "high"

    def test_one_second_apart_stays_exact(self):
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=T1 + timedelta(seconds=1),
                  source_label="src_b")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        assert rows["/b/torii_1024.jpg"].action == "EXACT"
        assert rows["/b/torii_1024.jpg"].match_confidence == "high"

    def test_missing_exif_on_one_side_stays_exact(self):
        keeper = _hr("/a/torii_0638.jpg", "s1", exif_date=T1, source_label="src_a")
        dup = _hr("/b/torii_1024.jpg", "s2", exif_date=None, source_label="src_b")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        assert rows["/b/torii_1024.jpg"].action == "EXACT"

    def test_two_pngs_same_phash_demoted_to_review_low(self):
        keeper = _hr("/a/shot_1.png", "p1", exif_date=T1, source_label="src_a", file_type="png")
        dup = _hr("/b/shot_2.png", "p2", exif_date=T1, source_label="src_b", file_type="png")
        rows = _rows(classify([keeper, dup], source_priority=PRIORITY))
        row = rows["/b/shot_2.png"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "low"
        assert "both screenshots" in row.reason
        assert all(r.action != "EXACT" for r in rows.values())


class TestNearDuplicateTimeGate:
    """pHash hamming 1..threshold with dHash agreeing — the near-dup path."""

    def test_near_dup_two_days_apart_demoted_to_review_low(self):
        a = _hr("/a/x.jpg", "n1", phash=BASE, dhash=BASE, exif_date=T1, source_label="src_a")
        b = _hr("/b/y.jpg", "n2", phash=NEAR, dhash=BASE,
                exif_date=T1 + timedelta(days=2), source_label="src_b")
        rows = _rows(classify([a, b], source_priority=PRIORITY))
        row = rows["/b/y.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "low"
        assert row.group_id == rows["/a/x.jpg"].group_id
        assert "; shot" in row.reason

    def test_same_near_dup_within_tolerance_stays_high(self):
        a = _hr("/a/x.jpg", "n1", phash=BASE, dhash=BASE, exif_date=T1, source_label="src_a")
        b = _hr("/b/y.jpg", "n2", phash=NEAR, dhash=BASE,
                exif_date=T1 + timedelta(seconds=2), source_label="src_b")
        rows = _rows(classify([a, b], source_priority=PRIORITY))
        row = rows["/b/y.jpg"]
        assert row.action == "REVIEW_DUPLICATE"
        assert row.match_confidence == "high"
        assert "; " not in row.reason

    def test_three_seconds_apart_is_past_tolerance_and_low(self):
        # Spec tolerance_s=2: a 3 s gap is not "agree" (not a half-hour multiple).
        a = _hr("/a/x.jpg", "n1", phash=BASE, dhash=BASE, exif_date=T1, source_label="src_a")
        b = _hr("/b/y.jpg", "n2", phash=NEAR, dhash=BASE,
                exif_date=T1 + timedelta(seconds=3), source_label="src_b")
        rows = _rows(classify([a, b], source_priority=PRIORITY))
        assert rows["/b/y.jpg"].match_confidence == "low"

    def test_two_png_near_dups_demoted_to_review_low(self):
        a = _hr("/a/x.png", "n1", phash=BASE, dhash=BASE, exif_date=T1,
                source_label="src_a", file_type="png")
        b = _hr("/b/y.png", "n2", phash=NEAR, dhash=BASE, exif_date=T1,
                source_label="src_b", file_type="png")
        rows = _rows(classify([a, b], source_priority=PRIORITY))
        row = rows["/b/y.png"]
        assert row.match_confidence == "low"
        assert row.reason.endswith("; both screenshots")


class TestShotTimeVote:
    """Direct cases for the tolerance + whole-half-hour rule."""

    def _pair(self, delta: timedelta) -> tuple[HashResult, HashResult]:
        return _hr("/a/1.jpg", "v1", exif_date=T1), _hr("/a/2.jpg", "v2", exif_date=T1 + delta)

    def test_five_and_a_half_hours_agrees(self):
        assert _shot_time_vote(*self._pair(timedelta(hours=5, minutes=30))) == "agree"

    def test_nine_and_a_half_hours_agrees(self):
        assert _shot_time_vote(*self._pair(timedelta(hours=9, minutes=30))) == "agree"

    def test_fourteen_hours_one_minute_disagrees(self):
        assert _shot_time_vote(*self._pair(timedelta(hours=14, minutes=1))) == "disagree"

    def test_thirty_six_hours_disagrees(self):
        assert _shot_time_vote(*self._pair(timedelta(hours=36))) == "disagree"

    def test_torii_gap_not_a_half_hour_multiple_disagrees(self):
        assert _shot_time_vote(*self._pair(timedelta(hours=13, minutes=46))) == "disagree"

    def test_missing_date_is_unknown(self):
        a = _hr("/a/1.jpg", "v1", exif_date=T1)
        b = _hr("/a/2.jpg", "v2", exif_date=None)
        assert _shot_time_vote(a, b) == "unknown"

    def test_screenshot_pair_requires_both_png(self):
        png = _hr("/a/1.png", "v1", file_type="png")
        jpg = _hr("/a/2.jpg", "v2", file_type="jpeg")
        assert _screenshot_pair(png, png) is True
        assert _screenshot_pair(png, jpg) is False


class TestAggressiveDeleteExcludesDemotedRows:
    """Composes classify() output into the real aggressive-delete selector."""

    def test_demoted_row_not_tagged_while_same_time_exact_is(self):
        keeper_path = str(Path("/a/keep.jpg"))
        keeper = _hr("/a/keep.jpg", "g0", exif_date=T1, source_label="src_a")
        same_time = _hr("/b/same.jpg", "g1", exif_date=T1 + timedelta(seconds=1),
                        source_label="src_b")
        demoted = _hr("/b/other_day.jpg", "g2", exif_date=T_NEXT_MORNING,
                      source_label="src_b")
        rows = classify([keeper, same_time, demoted], source_priority=PRIORITY)
        for r in rows:
            r.score = 0.5  # ranked peers: aggressive selector requires a score
        by_path = {r.source_path: r for r in rows}
        assert by_path[str(Path("/b/same.jpg"))].action == "EXACT"
        assert by_path[str(Path("/b/other_day.jpg"))].match_confidence == "low"

        tagged = non_keepers_for_aggressive_delete(rows, {keeper_path})

        assert str(Path("/b/same.jpg")) in tagged
        assert str(Path("/b/other_day.jpg")) not in tagged
