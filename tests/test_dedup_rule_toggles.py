"""PR-4a B1 — the burst (B5) and screenshot (B7) rule toggles in scanner/dedup.py.

These run against the GROUPING "9" default, so the toggles are tested by
comparing an explicit "off" call with the default "on" call. The defaults
stay on, and the default scan's grouping is unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from scanner.dedup import BURST_PREFIX, HashResult, classify
from tests.test_dedup_burst_stack import P0, P1, _five_frames
from tests.test_dedup_hint_edges import SHOT, T1, _group_sizes, _hr, _rows, _shot

PRIORITY = {"src_a": 0, "src_b": 1}


def _png(path: str, sha: str, phash: str, when: datetime = T1) -> HashResult:
    return _hr(path, sha, phash, when, "src_a", file_type="png")


def _group_map(rows: dict) -> dict[str, str | None]:
    return {key: row.group_id for key, row in rows.items()}


class TestBurstRule:
    """B5: ``burst_stacks=False`` keeps the classifier verdicts for burst frames."""

    def test_burst_off_keeps_classifier_verdicts(self):
        rows = _rows(classify(_five_frames(), source_priority=PRIORITY, burst_stacks=False))
        for name in ("f1.jpg", "f2.jpg", "f3.jpg", "f4.jpg"):
            row = rows[f"/a/{name}"]
            assert row.action == "REVIEW_DUPLICATE", name
            assert not row.reason.startswith(BURST_PREFIX), name

    def test_burst_off_leaves_group_ids_unchanged(self):
        frames = _five_frames()
        on = _rows(classify(frames, source_priority=PRIORITY, burst_stacks=True))
        off = _rows(classify(frames, source_priority=PRIORITY, burst_stacks=False))
        assert _group_map(off) == _group_map(on)


class TestScreenshotRule:
    """B7: ``screenshot_demotion=False`` lets two matching PNGs judge like photos."""

    def test_screenshot_demotion_off_format_pair_is_not_low(self):
        records = [
            _png("/s/a.png", "sha_a", P0),
            _png("/s/b.png", "sha_b", P0),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY, screenshot_demotion=False))
        duplicates = [r for r in rows.values() if r.action]  # the keeper carries no verdict
        assert len(duplicates) == 1
        assert duplicates[0].match_confidence == "high"
        assert "both screenshots" not in duplicates[0].reason

    def test_screenshot_demotion_off_near_dup_png_is_high(self):
        records = [
            _png("/s/a.png", "sha_a", P0),
            _png("/s/b.png", "sha_b", P1),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY, screenshot_demotion=False))
        flagged = [r for r in rows.values() if r.action == "REVIEW_DUPLICATE"]
        assert flagged and all(r.match_confidence == "high" for r in flagged)

    def test_screenshot_demotion_off_chain_forms_one_group(self):
        records = [_shot(name) for name in SHOT]
        rows = _rows(classify(records, source_priority=PRIORITY, screenshot_demotion=False))
        assert _group_sizes(rows) == [4]

    def test_screenshot_off_near_pngs_group_whatever_the_shot_times(self):
        later = T1 + timedelta(days=2)
        records = [
            _png("/s/a.png", "sha_a", P0, T1),
            _png("/s/b.png", "sha_b", P1, later),
        ]
        rows = _rows(classify(records, source_priority=PRIORITY, screenshot_demotion=False))
        # GROUPING 14: shot time is not read and B7 is off, so the near-dup pair
        # is judged like a photo: one group, a high REVIEW row.
        assert _group_sizes(rows) == [2]
        assert rows["/s/b.png"].action == "REVIEW_DUPLICATE"
        assert rows["/s/b.png"].match_confidence == "high"
        assert not [r for r in rows.values() if "shot" in r.reason]


class TestDefaultsAreGrouping10:
    """The default call is the grouping-10 behaviour, row for row."""

    def test_classify_defaults_are_grouping_10(self):
        frames = _five_frames() + [_shot(name) for name in SHOT]
        default = classify(frames, source_priority=PRIORITY)
        explicit = classify(
            frames, source_priority=PRIORITY, burst_stacks=True, screenshot_demotion=True
        )

        def snapshot(rows):
            return sorted(
                (r.source_path, r.action, r.reason, r.match_confidence, r.group_id)
                for r in rows
            )

        assert snapshot(default) == snapshot(explicit)
