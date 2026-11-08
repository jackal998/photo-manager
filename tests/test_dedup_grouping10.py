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
from scanner.grey_store import GreyBytes, GreySig
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
    grey128: GreySig | None = GreyBytes(bytes(16384)),
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
        grey128=grey128,
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
        # Same colour signature; the grey structure differs in a 12x12 patch.
        patch = bytes(60 if (i // 128 < 12 and i % 128 < 12) else 0 for i in range(16384))
        jpg = _hr("/b/x.jpg", "jx", PA, None, T0, "src_b", ftype="jpeg",
                  grey128=GreyBytes(patch))
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

    def test_b7_on_pixel_identical_pngs_group_exact(self):
        # GROUPING 12 flip: these two PNGs carry identical rgb32 and grey128
        # (the fixture default), so they are pixel-identical copies. The screenshot
        # rule keeps a non-identical PNG pair a hint (TestGrouping12PngPixels).
        rows = _rows(classify(self._pngs(), source_priority=PRIORITY))
        assert _group_sizes(rows) == [2]
        dup = rows["/s/two.png"]
        assert dup.action == "EXACT"
        assert dup.match_confidence == "high"
        assert "png vs png" in dup.reason, dup.reason

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


def _assert_partner_invariant(result: list) -> None:
    """Every EXACT or REVIEW_DUPLICATE row names a partner that shares its group."""
    by_path = {r.source_path: r for r in result}
    for row in result:
        if row.action in ("EXACT", "REVIEW_DUPLICATE"):
            assert row.duplicate_of is not None, row.source_path
            partner = by_path[row.duplicate_of]
            assert row.group_id is not None and row.group_id == partner.group_id, (
                row.source_path, row.reason)


class TestUndatedRowNeverLinksTwoMoments:
    """GROUPING 10 iteration 3: an undated row has no shot time, so a content
    edge through it must not join two dated rows shot at different moments.
    Identity edges (EXACT copies) ignore dates altogether."""

    T_JAN = datetime(2024, 1, 31, 18, 0, 0)
    T_MAY = datetime(2024, 5, 6, 9, 30, 0)
    # 8 bits from PA, 12 from PNEAR: a near-duplicate of PA, not of PNEAR.
    P_FAR = "ff000000ffffffff"

    def test_undated_bridge_is_refused_and_the_far_moment_is_a_hint(self):
        records = [
            _hr("/m/a_undated.jpg", "ua", PA, PA, None),
            _hr("/m/b_jan.jpg", "jb", PNEAR, PA, self.T_JAN),
            _hr("/m/c_may.jpg", "kc", self.P_FAR, PA, self.T_MAY),
        ]
        result = classify(records, source_priority=PRIORITY)
        rows = _rows(result)
        a, b, c = rows["/m/a_undated.jpg"], rows["/m/b_jan.jpg"], rows["/m/c_may.jpg"]
        assert a.group_id is not None and a.group_id == b.group_id
        assert c.group_id is None
        assert c.match_confidence == "low"
        assert HINT_ONLY_PREFIX in c.reason, c.reason
        # F1: the refused row keeps no duplicate verdict naming an ungrouped partner.
        assert c.action != "REVIEW_DUPLICATE", c.reason
        assert c.duplicate_of is None
        assert c.reason.startswith("unique; " + HINT_ONLY_PREFIX), c.reason
        # F2: one hint per pair, so the undated row names its partner once.
        assert "+1 more" not in a.reason, a.reason
        assert "a_undated.jpg" in c.reason, c.reason
        assert "hint:" in a.reason, a.reason
        _assert_partner_invariant(result)

    def test_refused_row_edge_resets_a_row_that_then_joins_another_group(self):
        """Case (i): the undated row A is a near-duplicate of the May row X
        (joins it, a high edge) and of the Jan row B (its own row link, refused).
        A must not keep the verdict that names B."""
        # The near-duplicate pass visits rows in path order. X (lower priority,
        # sorted first) is flagged to A first; then B (sorted before A) is A's
        # keeper and flags A, so A's own link is to B. Row edges sort X-A before
        # A-B, so A joins X, and its link to B is refused.
        records = [
            _hr("/m/c_a_undated.jpg", "ua", PNEAR, PA, None, "src_a"),
            _hr("/m/a_x_may.jpg", "xx", PA, PA, self.T_MAY, "src_b"),
            _hr("/m/b_b_jan.jpg", "jb", "ff000000fffffff0", PA, self.T_JAN, "src_a"),
        ]
        result = classify(records, source_priority=PRIORITY)
        rows = _rows(result)
        x, a, b = rows["/m/a_x_may.jpg"], rows["/m/c_a_undated.jpg"], rows["/m/b_b_jan.jpg"]
        assert a.group_id is not None and a.group_id == x.group_id
        assert a.duplicate_of is None
        assert a.action == "UNDATED", a.reason
        assert "near-duplicate" not in a.reason, a.reason
        assert b.group_id is None
        assert HINT_ONLY_PREFIX in b.reason, b.reason
        _assert_partner_invariant(result)

    def test_agreeing_moments_group_all_three(self):
        """The rule does not fire when the two dated moments agree (2 s apart,
        the BURST_MAX_GAP_S tolerance)."""
        records = [
            _hr("/m/a_undated.jpg", "ua", PA, PA, None),
            _hr("/m/b_jan.jpg", "jb", PNEAR, PA, self.T_JAN),
            _hr("/m/c_jan_2s.jpg", "kc", self.P_FAR, PA,
                self.T_JAN + timedelta(seconds=2)),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [3]
        assert all(r.match_confidence != "low" for r in rows.values())

    def test_undated_format_duplicate_groups_through_identity(self):
        """Identity edges ignore dates: an undated JPG that is a pixel-identical
        copy of a dated HEIC joins the HEIC's group, whose dated near-duplicate
        is from the same moment."""
        records = [
            _hr("/m/h.heic", "hh", PA, PA, self.T_JAN, ftype="heic"),
            _hr("/m/j_undated.jpg", "jj", PA, None, None, "src_b", ftype="jpeg"),
            _hr("/m/n_same_moment.jpg", "nn", PNEAR, PA, self.T_JAN),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        j = rows["/m/j_undated.jpg"]
        assert j.action == "EXACT"
        assert j.group_id is not None
        assert j.group_id == rows["/m/h.heic"].group_id
        assert j.group_id == rows["/m/n_same_moment.jpg"].group_id

    def test_undated_pair_joins_a_dated_component(self):
        """Both ends undated: one already in a dated component, the other alone.
        No second moment exists to disagree, so the edge unions."""
        records = [
            _hr("/m/x_jan.jpg", "xx", PA, PA, self.T_JAN),
            _hr("/m/u1_undated.jpg", "u1", PNEAR, PA, None),
            _hr("/m/u2_undated.jpg", "u2", "ff000000fffffff0", PA, None),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY))
        assert _group_sizes(rows) == [3]


# GROUPING 12: a pixel-identical PNG pair is EXACT even while B7 is on. A PNG
# pHash match that is not pixel-identical stays a hint, and the shot-time rule
# still wins over both.
_GREY_PATCH_12 = GreyBytes(
    bytes(60 if (i // 128 < 12 and i % 128 < 12) else 0 for i in range(16384))
)


def _png(path: str, sha: str, label: str, exif=T0, phash=PA, grey=None):
    return _hr(path, sha, phash, PA, exif, label, "png",
               grey128=grey if grey is not None else GreyBytes(bytes(16384)))


class TestGrouping12PngPixels:
    def test_b7_on_pixel_identical_pngs_are_exact_and_grouped(self):
        rows = _rows(classify(
            [_png("/s/one.png", "p1", "src_a"), _png("/t/two.png", "p2", "src_b")],
            source_priority=PRIORITY,
        ))
        dup = rows["/t/two.png"]
        assert dup.action == "EXACT"
        assert dup.match_confidence == "high"
        assert "png vs png" in dup.reason, dup.reason
        assert _group_sizes(rows) == [2]

    def test_b7_on_non_identical_pngs_stay_a_hint(self):
        rows = _rows(classify(
            [_png("/s/one.png", "p1", "src_a"),
             _png("/t/two.png", "p2", "src_b", grey=_GREY_PATCH_12)],
            source_priority=PRIORITY,
        ))
        assert _group_sizes(rows) == []
        for path in ("/s/one.png", "/t/two.png"):
            assert rows[path].group_id is None
            assert "same pHash, both screenshots" in rows[path].reason, rows[path].reason

    def test_b7_on_png_pixel_check_unavailable_stays_a_hint(self):
        # One side has no grey signature, so identity is unknown, not proven:
        # the pair keeps the screenshot hint and is not grouped.
        unknown = _hr("/t/two.png", "p2", PA, PA, T0, "src_b", "png", grey128=None)
        rows = _rows(classify(
            [_png("/s/one.png", "p1", "src_a"), unknown],
            source_priority=PRIORITY,
        ))
        assert _group_sizes(rows) == []
        for path in ("/s/one.png", "/t/two.png"):
            assert rows[path].group_id is None
            assert "same pHash, both screenshots" in rows[path].reason, rows[path].reason

    def test_b7_on_shot_time_disagreement_wins_over_identical_pixels(self):
        # Seven minutes apart: not within the same-moment window, not a half-hour
        # multiple, so the shot-time vote disagrees and names the gap.
        rows = _rows(classify(
            [_png("/s/one.png", "p1", "src_a"),
             _png("/t/two.png", "p2", "src_b", exif=T0 + timedelta(minutes=7))],
            source_priority=PRIORITY,
        ))
        assert _group_sizes(rows) == []
        dup = rows["/t/two.png"]
        assert dup.action == ""
        assert "shot" in dup.reason and "both screenshots" not in dup.reason, dup.reason

    def test_b7_on_near_duplicate_pngs_stay_a_hint(self):
        # pHash hamming 4 (PNEAR): not the exact-hash path, so the pixel match
        # cannot make it EXACT.
        rows = _rows(classify(
            [_png("/s/one.png", "p1", "src_a"),
             _png("/t/two.png", "p2", "src_b", phash=PNEAR)],
            source_priority=PRIORITY,
        ))
        assert _group_sizes(rows) == []
        assert rows["/t/two.png"].action == ""
        assert rows["/t/two.png"].group_id is None

    def test_b7_off_pixel_identical_pngs_judged_as_photos(self):
        rows = _rows(classify(
            [_png("/s/one.png", "p1", "src_a"), _png("/t/two.png", "p2", "src_b")],
            source_priority=PRIORITY, screenshot_demotion=False,
        ))
        assert rows["/t/two.png"].action == "EXACT"
        assert _group_sizes(rows) == [2]

    def test_b7_off_non_identical_pngs_judged_as_photos(self):
        rows = _rows(classify(
            [_png("/s/one.png", "p1", "src_a"),
             _png("/t/two.png", "p2", "src_b", grey=_GREY_PATCH_12)],
            source_priority=PRIORITY, screenshot_demotion=False,
        ))
        dup = rows["/t/two.png"]
        assert dup.action == "REVIEW_DUPLICATE"
        assert "pixels differ" in dup.reason, dup.reason
