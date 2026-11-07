"""GROUPING_STRATEGY_VERSION "10" — low-confidence evidence never groups.

Principle A (docs/audits/dedup-core-research-2026-11-05/grouping10-design.md):
only byte identity, two agreeing content signals, or a device-written identity
union. Anything weaker is a hint: it is shown as text on both files, and it
never creates, grows or merges a group or sets an action.

Principle C: two PNGs group only as byte copies while B7 (screenshot demotion)
is on. A PNG pHash match is a hint.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from scanner.dedup import BURST_PREFIX, HINT_ONLY_PREFIX, HashResult, classify
from scanner.walker import FileRecord

T0 = datetime(2024, 6, 12, 20, 38, 0)
T_PLUS_36H = T0 + timedelta(hours=36)
T_NEXT_MORNING = datetime(2024, 6, 13, 10, 24, 0)   # 13 h 46 min after T0
T_17_02 = datetime(2024, 6, 12, 17, 2, 0)           # 3 h 36 min before T0

PRIORITY = {"src_a": 0, "src_b": 1}

PA = "00000000ffffffff"
PNEAR = "00000000fffffff0"     # hamming 4 from PA: a pHash near-duplicate
PB_MOMENT = "00000000fffffffc"  # hamming 2 from PA
PC_MOMENT = "00000000ffffffcf"  # hamming 2 from PA, 4 from PB_MOMENT
BURST_HASH = "0f0f0f0f0f0f0f0f"


def _hr(
    path: str,
    sha: str,
    phash: str | None = None,
    dhash: str | None = None,
    exif: datetime | None = T0,
    label: str = "src_a",
    ftype: str = "jpeg",
    rgb32: bytes | None = bytes(3072),
    pair_cluster: tuple[str, ...] = (),
) -> HashResult:
    return HashResult(
        record=FileRecord(
            path=Path(path), source_label=label, file_type=ftype,
            pair_cluster=tuple(Path(p) for p in pair_cluster),
        ),
        sha256=sha,
        phash=phash,
        dhash=dhash,
        mean_color=None,
        exif_date=exif,
        pixel_width=4000,
        pixel_height=3000,
        rgb32=rgb32,
    )


def _rows(result: list) -> dict:
    return {Path(r.source_path).as_posix(): r for r in result}


def _group_sizes(rows: dict) -> list[int]:
    return sorted(Counter(r.group_id for r in rows.values() if r.group_id).values())


def _low_only_pair() -> list[HashResult]:
    """Two lone JPEGs, pHash 4 bits apart, dHash agreeing, shot 36 h apart."""
    return [
        _hr("/lo/one.jpg", "lo1", PA, PA, T0),
        _hr("/lo/two.jpg", "lo2", PNEAR, PA, T_PLUS_36H),
    ]


def _torii() -> list[HashResult]:
    """Three moments of one scene, each two byte-identical copies."""
    return [
        _hr("/to/a_0638.jpg", "t1", PA, PA, T0, "src_a"),
        _hr("/to/b_0638.jpg", "t1", PA, PA, T0, "src_b"),
        _hr("/to/a_1024.jpg", "t2", PB_MOMENT, PA, T_NEXT_MORNING, "src_a"),
        _hr("/to/b_1024.jpg", "t2", PB_MOMENT, PA, T_NEXT_MORNING, "src_b"),
        _hr("/to/a_1702.jpg", "t3", PC_MOMENT, PA, T_17_02, "src_a"),
        _hr("/to/b_1702.jpg", "t3", PC_MOMENT, PA, T_17_02, "src_b"),
    ]


def _portrait_and_screenshots() -> list[HashResult]:
    """HEIC + MP4 with no ContentIdentifier, two byte-identical PNGs, and one
    PNG near-duplicate of them."""
    return [
        _hr("/po/IMG_1.HEIC", "ph", None, None, T0, ftype="heic",
            pair_cluster=("/po/IMG_1.MP4",)),
        _hr("/po/IMG_1.MP4", "pm", None, None, T0, ftype="mp4",
            pair_cluster=("/po/IMG_1.HEIC",)),
        _hr("/sc/a.png", "pa", PA, PA, T0, ftype="png"),
        _hr("/sc/a_copy.png", "pa", PA, PA, T0, "src_b", ftype="png"),
        _hr("/sc/c.png", "pc", PNEAR, PA, T0, ftype="png"),
    ]


def _burst() -> list[HashResult]:
    """Five frames two seconds apart, byte-different, same pixels."""
    return [
        _hr(f"/bu/f{k}.jpg", f"bu{k}", BURST_HASH, BURST_HASH, T0 + timedelta(seconds=2 * k))
        for k in range(5)
    ]


def _mixed() -> list[HashResult]:
    return _low_only_pair() + _torii() + _portrait_and_screenshots() + _burst()


class TestLowOnlyEvidenceNeverGroups:
    def test_low_only_pair_is_two_hints_not_a_group(self):
        rows = _rows(classify(_low_only_pair(), source_priority=PRIORITY))
        for path, partner in (("/lo/one.jpg", "two.jpg"), ("/lo/two.jpg", "one.jpg")):
            row = rows[path]
            assert row.group_id is None
            assert row.action == ""
            assert row.match_confidence == "low"
            assert HINT_ONLY_PREFIX in row.reason, row.reason
            assert partner in row.reason, row.reason
            assert "h apart" in row.reason, row.reason

    def test_lone_file_next_to_byte_copy_pair_stays_ungrouped(self):
        pair = [
            _hr("/a/p.jpg", "sp", PA, PA, T0, "src_a"),
            _hr("/b/p_copy.jpg", "sp", PA, PA, T0, "src_b"),
        ]
        lone = _hr("/c/lone.jpg", "sl", PNEAR, PA, T_PLUS_36H, "src_a")
        rows = _rows(classify(pair + [lone], source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]
        assert rows["/c/lone.jpg"].group_id is None
        assert rows["/c/lone.jpg"].match_confidence == "low"
        assert rows["/a/p.jpg"].group_id == rows["/b/p_copy.jpg"].group_id


class TestTorii:
    def test_three_moments_stay_three_groups_with_no_review_row(self):
        rows = _rows(classify(_torii(), source_priority=PRIORITY))
        assert _group_sizes(rows) == [2, 2, 2]
        assert not [r for r in rows.values() if r.action == "REVIEW_DUPLICATE"]

    def test_keepers_name_their_hint_and_keep_their_confidence(self):
        rows = _rows(classify(_torii(), source_priority=PRIORITY))
        keepers = [r for r in rows.values() if r.action != "EXACT"]
        assert len(keepers) == 3
        for row in keepers:
            assert "hint: low-confidence match with" in row.reason, row.reason
            assert row.match_confidence != "low", row.reason


class TestPortraitAndScreenshots:
    def test_only_the_byte_copy_png_pair_groups(self):
        rows = _rows(classify(_portrait_and_screenshots(), source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]
        for path in ("/po/IMG_1.HEIC", "/po/IMG_1.MP4", "/sc/c.png"):
            assert rows[path].group_id is None, path
        assert rows["/sc/a.png"].group_id == rows["/sc/a_copy.png"].group_id
        assert HINT_ONLY_PREFIX in rows["/sc/c.png"].reason


class TestInvariantsOverTheMixedFixture:
    def test_no_grouped_row_is_low_confidence(self):
        rows = _rows(classify(_mixed(), source_priority=PRIORITY))
        grouped = [r for r in rows.values() if r.group_id is not None]
        assert grouped, "the mixed fixture must produce groups, else this is vacuous"
        for row in grouped:
            assert row.match_confidence != "low", row.source_path

    def test_burst_frames_form_one_stack_of_five(self):
        """The mixed fixture really contains a burst: five pHash-identical frames
        two seconds apart are one moment, one group, and one keep-all stack."""
        rows = _rows(classify(_mixed(), source_priority=PRIORITY))
        frames = [rows[f"/bu/f{k}.jpg"] for k in range(5)]
        assert frames[0].group_id is not None
        assert len({f.group_id for f in frames}) == 1
        for frame in frames:
            assert frame.reason.startswith(BURST_PREFIX), frame.reason

    def test_twenty_shuffles_give_identical_rows(self):
        def snapshot(rows: list) -> dict:
            return {
                Path(r.source_path).as_posix():
                    (r.group_id, r.action, r.reason, r.match_confidence)
                for r in rows
            }

        base = snapshot(classify(_mixed(), source_priority=PRIORITY))
        for seed in range(20):
            records = _mixed()
            random.Random(seed).shuffle(records)
            assert snapshot(classify(records, source_priority=PRIORITY)) == base, seed


class TestPixelIdentityIsASecondSignal:
    def test_same_pixels_without_dhash_is_an_exact_group(self):
        heic = _hr("/a/x.heic", "hx", PA, None, T0, ftype="heic")
        jpg = _hr("/b/x.jpg", "jx", PA, None, T0, "src_b", ftype="jpeg")
        rows = _rows(classify([heic, jpg], source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]
        duplicates = [r for r in rows.values() if r.action == "EXACT"]
        assert len(duplicates) == 1
        assert duplicates[0].match_confidence == "high"

    def test_different_pixels_without_dhash_is_a_hint(self):
        heic = _hr("/a/x.heic", "hx", PA, None, T0, ftype="heic")
        jpg = _hr("/b/x.jpg", "jx", PA, None, T0, "src_b", ftype="jpeg",
                  rgb32=bytes([60]) * 3072)
        rows = _rows(classify([heic, jpg], source_priority=PRIORITY))
        assert _group_sizes(rows) == []
        dup = rows["/b/x.jpg"]
        assert dup.action == ""
        assert dup.match_confidence == "low"
        assert "pixels differ" in dup.reason, dup.reason
        assert "dHash unavailable" in dup.reason, dup.reason


class TestScreenshotRuleB7Contract:
    """G8: two PNGs with pHash 0, distinct bytes and equal pixels. B7 on makes
    the pair a hint that names the screenshot rule. B7 off judges them like
    photos, so they are an EXACT group."""

    def _pngs(self) -> list[HashResult]:
        return [
            _hr("/s/one.png", "p1", PA, PA, T0, "src_a", ftype="png"),
            _hr("/s/two.png", "p2", PA, PA, T0, "src_b", ftype="png"),
        ]

    def test_b7_on_is_a_hint_naming_both_screenshots(self):
        rows = _rows(classify(self._pngs(), source_priority=PRIORITY))
        assert _group_sizes(rows) == []
        for path in ("/s/one.png", "/s/two.png"):
            assert rows[path].group_id is None
            assert "both screenshots" in rows[path].reason, rows[path].reason

    def test_b7_off_judges_them_like_photos_and_groups_exact(self):
        rows = _rows(classify(
            self._pngs(), source_priority=PRIORITY, screenshot_demotion=False,
        ))
        assert _group_sizes(rows) == [2]
        dup = [r for r in rows.values() if r.action == "EXACT"]
        assert len(dup) == 1
        assert dup[0].match_confidence == "high"


class TestTwoShotMomentsInOneBucket:
    """Review finding MEDIUM-1: an all-lossy pHash-0 bucket with two moments.
    Each moment keeps its own pair; the old single keeper lost the second."""

    T_MOMENT_B = T0 + timedelta(hours=34)

    def test_each_moment_keeps_its_own_exact_pair_with_a_cross_moment_hint(self):
        records = [
            _hr("/m/a.heic", "ah", PA, None, T0, ftype="heic"),
            _hr("/m/a.jpg", "aj", PA, None, T0, ftype="jpeg"),
            _hr("/m/b.heic", "bh", PA, None, self.T_MOMENT_B, ftype="heic"),
            _hr("/m/b.jpg", "bj", PA, None, self.T_MOMENT_B, ftype="jpeg"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [2, 2]
        for row in rows.values():
            if row.group_id is not None:
                assert row.match_confidence != "low", row.source_path
        for group_id in {r.group_id for r in rows.values()}:
            members = [r for r in rows.values() if r.group_id == group_id]
            keepers = [r for r in members if r.action != "EXACT"]
            copies = [r for r in members if r.action == "EXACT"]
            assert len(keepers) == 1 and len(copies) == 1, group_id
            assert copies[0].match_confidence == "high"
        for keeper in (r for r in rows.values() if r.action != "EXACT"):
            assert "shot 34.0 h apart" in keeper.reason, keeper.reason


class TestHintTextAndReclaim:
    def test_hint_text_counts_the_partners_beyond_the_first(self):
        """LOW-4: a lone file hinted by three partners names the first by sorted
        path and counts the other two."""
        lone = _hr("/x/x.jpg", "xx", PA, PA, T0)
        # Offsets are not whole half-hours: a 12 h gap would read as a timezone
        # shift and merge the partners into one moment.
        partners = [
            _hr("/p/p1.jpg", "p1", PNEAR, PA, T0 + timedelta(hours=36)),
            _hr("/p/p2.jpg", "p2", PNEAR, PA, T0 + timedelta(hours=36, minutes=7)),
            _hr("/p/p3.jpg", "p3", PNEAR, PA, T0 + timedelta(hours=36, minutes=13)),
        ]
        rows = _rows(classify([lone] + partners, source_priority=PRIORITY))
        reason = rows["/x/x.jpg"].group_id, rows["/x/x.jpg"].reason
        assert reason[0] is None
        assert "low-confidence match with p1.jpg" in reason[1], reason[1]
        assert "+2 more" in reason[1], reason[1]

    def test_high_partner_still_gives_the_review_row_when_a_low_partner_sorts_first(self):
        """LOW-4 (reclaim): L sorts before H and is a low hint to X. X still gets
        the high REVIEW row from H, because a hint never claims a row."""
        x = _hr("/c/x.jpg", "xx", PA, PA, T0, "src_b")
        h = _hr("/b/h.jpg", "hh", PNEAR, PA, T0, "src_a")
        lo = _hr("/a/l.jpg", "ll", "00000000ffff0ff0", PA,
                 T0 + timedelta(hours=36), "src_a")
        rows = _rows(classify([x, h, lo], source_priority=PRIORITY))
        row_x = rows["/c/x.jpg"]
        assert row_x.action == "REVIEW_DUPLICATE", row_x.reason
        assert row_x.match_confidence == "high"
        assert row_x.duplicate_of.endswith("h.jpg")
        assert row_x.group_id is not None
        assert row_x.group_id == rows["/b/h.jpg"].group_id
        assert rows["/a/l.jpg"].group_id is None
        assert rows["/a/l.jpg"].match_confidence == "low"


class TestShotGapText:
    def test_a_two_minute_gap_is_named_in_minutes(self):
        """LOW-3: a gap under an hour prints as minutes, not as "0.0 h"."""
        records = [
            _hr("/m/a.heic", "ah", PA, None, T0, ftype="heic"),
            _hr("/m/a.jpg", "aj", PA, None, T0, ftype="jpeg"),
            _hr("/m/b.heic", "bh", PA, None, T0 + timedelta(minutes=2), ftype="heic"),
            _hr("/m/b.jpg", "bj", PA, None, T0 + timedelta(minutes=2), ftype="jpeg"),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        keepers = [r for r in rows.values() if r.action != "EXACT"]
        assert len(keepers) == 2
        for keeper in keepers:
            assert "shot 2 min apart" in keeper.reason, keeper.reason
