"""Tests for core/app_service/action_resolve.py.

Near-verbatim port of:
  - TestThresholdSelectionLogic  (test_select_dialog.py:1795-1871)
  - TestTopNSelectionLogic       (test_select_dialog.py:1874-1952)
  - TestPatternEncoding          (test_select_dialog.py:1955-1979)

retargeted to import from core.app_service.action_resolve (Qt-free copy).

Additional tests cover the unified resolve_matched_paths dispatcher,
including the load-bearing empty-pattern guard (pinned by
test_file_operations.py:1523).
"""

from __future__ import annotations

from dataclasses import dataclass, field as _f
from datetime import datetime


# ---------------------------------------------------------------------------
# Duck-typed test fixtures (no Qt)
# ---------------------------------------------------------------------------

def _make_record(
    *, file_path: str, file_size_bytes: int = 0,
    score: float | None = None, hamming_distance: int | None = None,
    creation_date=None, shot_date=None, is_locked: bool = False,
    folder_path: str = "", user_decision: str = "",
    pixel_width: int | None = None, pixel_height: int | None = None,
):
    """Minimal duck-typed record matching what the helpers read."""
    @dataclass
    class _Rec:
        file_path: str
        file_size_bytes: int
        score: float | None
        hamming_distance: int | None
        creation_date: object
        shot_date: object
        is_locked: bool
        folder_path: str
        user_decision: str
        pixel_width: object
        pixel_height: object

    return _Rec(
        file_path=file_path,
        file_size_bytes=file_size_bytes,
        score=score,
        hamming_distance=hamming_distance,
        creation_date=creation_date,
        shot_date=shot_date,
        is_locked=is_locked,
        folder_path=folder_path,
        user_decision=user_decision,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
    )


def _make_group(items):
    @dataclass
    class _G:
        items: list = _f(default_factory=list)

    return _G(items=list(items))


# ---------------------------------------------------------------------------
# TestThresholdSelectionLogic
# ---------------------------------------------------------------------------

class TestThresholdSelectionLogic:
    """select_paths_by_threshold returns the rows the user actually expects."""

    def test_size_gt_selects_only_above(self):
        # 3 rows at 50/100/200 bytes; "> 100" must select exactly
        # one row (200), not two. Regression: an `>=` slip would
        # delete one extra file.
        from core.app_service.action_resolve import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", file_size_bytes=50),
            _make_record(file_path="a/2.jpg", file_size_bytes=100),
            _make_record(file_path="a/3.jpg", file_size_bytes=200),
        ])
        result = select_paths_by_threshold([g], "Size (Bytes)", ">", "100")
        assert result == ["a/3.jpg"]

    def test_size_ge_includes_equal(self):
        from core.app_service.action_resolve import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", file_size_bytes=50),
            _make_record(file_path="a/2.jpg", file_size_bytes=100),
            _make_record(file_path="a/3.jpg", file_size_bytes=200),
        ])
        result = select_paths_by_threshold([g], "Size (Bytes)", ">=", "100")
        assert result == ["a/2.jpg", "a/3.jpg"]

    def test_score_threshold_with_none_skipped(self):
        # Records with score=None (isolated rows, MOV passengers)
        # must NOT match a `> 0` threshold — the brief's score
        # contract treats None as "unrankable", not "zero".
        # Regression: a `float(None or 0)` would falsely include passengers.
        from core.app_service.action_resolve import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", score=None),
            _make_record(file_path="a/2.jpg", score=0.4),
            _make_record(file_path="a/3.jpg", score=0.8),
        ])
        result = select_paths_by_threshold([g], "Score", ">", "0.5")
        assert result == ["a/3.jpg"]

    def test_creation_date_isodate_threshold(self):
        from core.app_service.action_resolve import select_paths_by_threshold

        g = _make_group([
            _make_record(
                file_path="a/old.jpg",
                creation_date=datetime(2020, 1, 1),
            ),
            _make_record(
                file_path="a/new.jpg",
                creation_date=datetime(2026, 6, 1),
            ),
        ])
        result = select_paths_by_threshold(
            [g], "Creation Date", ">", "2024-01-01"
        )
        assert result == ["a/new.jpg"]

    def test_unparseable_threshold_returns_empty(self):
        # User typed "abc" in the value field — must select nothing,
        # not crash.
        from core.app_service.action_resolve import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", file_size_bytes=100),
        ])
        result = select_paths_by_threshold([g], "Size (Bytes)", ">", "abc")
        assert result == []


# ---------------------------------------------------------------------------
# TestTopNSelectionLogic
# ---------------------------------------------------------------------------

class TestTopNSelectionLogic:
    """select_paths_top_n ranks within group, not globally."""

    def test_top_1_per_group_picks_one_from_each(self):
        # Two groups of two rows each; Top 1 by score per group
        # picks the higher-scoring row from EACH group — not globally.
        from core.app_service.action_resolve import select_paths_top_n

        g1 = _make_group([
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.9),
        ])
        g2 = _make_group([
            _make_record(file_path="g2/a.jpg", score=0.4),
            _make_record(file_path="g2/b.jpg", score=0.6),
        ])
        result = select_paths_top_n([g1, g2], "Score", 1, "desc")
        assert sorted(result) == ["g1/b.jpg", "g2/b.jpg"]

    def test_bottom_1_picks_lowest_per_group(self):
        from core.app_service.action_resolve import select_paths_top_n

        g1 = _make_group([
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.9),
        ])
        g2 = _make_group([
            _make_record(file_path="g2/a.jpg", score=0.4),
            _make_record(file_path="g2/b.jpg", score=0.6),
        ])
        result = select_paths_top_n([g1, g2], "Score", 1, "asc")
        assert sorted(result) == ["g1/a.jpg", "g2/a.jpg"]

    def test_top_n_skips_none_scored_records(self):
        # MOV passengers and isolated rows have score=None and must
        # NOT be ranked.
        from core.app_service.action_resolve import select_paths_top_n

        g = _make_group([
            _make_record(file_path="g1/p.mov", score=None),
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.7),
        ])
        result = select_paths_top_n([g], "Score", 1, "desc")
        assert result == ["g1/b.jpg"]

    def test_top_n_larger_than_group_returns_all(self):
        # Top 5 of a 2-row group returns both.
        from core.app_service.action_resolve import select_paths_top_n

        g = _make_group([
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.9),
        ])
        result = select_paths_top_n([g], "Score", 5, "desc")
        assert sorted(result) == ["g1/a.jpg", "g1/b.jpg"]

    def test_top_n_tie_is_stable_by_path(self):
        # Two equal scores → tiebreak on file_path.  Stable selection
        # matters because re-running the same Top-N must select the same rows.
        from core.app_service.action_resolve import select_paths_top_n

        g = _make_group([
            _make_record(file_path="g1/b.jpg", score=0.5),
            _make_record(file_path="g1/a.jpg", score=0.5),
            _make_record(file_path="g1/c.jpg", score=0.5),
        ])
        # Top 1 of three equal-score rows: tiebreak picks the
        # alphabetically-first path (a.jpg), stably across runs.
        result = select_paths_top_n([g], "Score", 1, "desc")
        assert result == ["g1/a.jpg"]


# ---------------------------------------------------------------------------
# TestPatternEncoding
# ---------------------------------------------------------------------------

class TestPatternEncoding:
    """encode/decode round-trips for the special pattern strings."""

    def test_cmp_pattern_round_trip(self):
        from core.app_service.action_resolve import (
            decode_cmp_pattern, encode_cmp_pattern,
        )
        assert decode_cmp_pattern(encode_cmp_pattern(">=", "80")) == (">=", "80")

    def test_top_n_pattern_round_trip(self):
        from core.app_service.action_resolve import (
            decode_top_n_pattern, encode_top_n_pattern,
        )
        assert decode_top_n_pattern(encode_top_n_pattern(3, "desc")) == (3, "desc")

    def test_decode_cmp_returns_none_for_non_cmp(self):
        from core.app_service.action_resolve import decode_cmp_pattern
        assert decode_cmp_pattern("IMG.*") is None

    def test_decode_top_n_rejects_bad_order(self):
        # The receiver guards against a corrupted pattern so a future drift
        # doesn't silently rank with bogus order.  None signals "no-match".
        from core.app_service.action_resolve import decode_top_n_pattern
        assert decode_top_n_pattern("__top_n__:3:garbage") is None

    def test_cmp_pattern_with_datetime_colon_roundtrips(self):
        # Value text may contain ':' (ISO timestamp with seconds) — the
        # encode/decode split on the FIRST ':' after the op so the full
        # value survives the round-trip.
        from core.app_service.action_resolve import (
            decode_cmp_pattern, encode_cmp_pattern,
        )
        encoded = encode_cmp_pattern(">", "2026-01-01 12:00:00")
        assert decode_cmp_pattern(encoded) == (">", "2026-01-01 12:00:00")


# ---------------------------------------------------------------------------
# TestResolveMatchedPaths — unified dispatcher
# ---------------------------------------------------------------------------

class TestResolveMatchedPaths:
    """resolve_matched_paths dispatcher: empty-pattern guard + routing."""

    def test_empty_pattern_returns_empty_list(self):
        # LOAD-BEARING: re.search("", x) is truthy for any string, so an
        # empty pattern without this guard would match every row and tag
        # every file for whatever decision the caller applies (e.g. delete).
        # Pinned by test_file_operations.py:1523.
        from core.app_service.action_resolve import resolve_matched_paths

        g = _make_group([
            _make_record(file_path="a/1.jpg"),
            _make_record(file_path="a/2.jpg"),
        ])
        result = resolve_matched_paths([g], "File Name", "")
        assert result == []

    def test_plain_regex_case_insensitive(self):
        from core.app_service.action_resolve import resolve_matched_paths

        g = _make_group([
            _make_record(file_path="/photos/IMG_001.jpg"),
            _make_record(file_path="/photos/DSC_002.jpg"),
        ])
        result = resolve_matched_paths([g], "File Name", r"img_\d+")
        assert result == ["/photos/IMG_001.jpg"]

    def test_cmp_pattern_dispatches_to_threshold(self):
        from core.app_service.action_resolve import (
            encode_cmp_pattern, resolve_matched_paths,
        )
        g = _make_group([
            _make_record(file_path="a/low.jpg", score=0.2),
            _make_record(file_path="a/high.jpg", score=0.9),
        ])
        pattern = encode_cmp_pattern(">", "0.5")
        result = resolve_matched_paths([g], "Score", pattern)
        assert result == ["a/high.jpg"]

    def test_top_n_pattern_dispatches_to_top_n(self):
        from core.app_service.action_resolve import (
            encode_top_n_pattern, resolve_matched_paths,
        )
        g = _make_group([
            _make_record(file_path="a/low.jpg", score=0.2),
            _make_record(file_path="a/high.jpg", score=0.9),
        ])
        pattern = encode_top_n_pattern(1, "desc")
        result = resolve_matched_paths([g], "Score", pattern)
        assert result == ["a/high.jpg"]

    def test_plain_regex_group_then_record_order(self):
        # Output order mirrors file_operations.py:1387-1408:
        # group-major, record-minor (insertion order within each group).
        from core.app_service.action_resolve import resolve_matched_paths

        g1 = _make_group([
            _make_record(file_path="/p/b.jpg"),
            _make_record(file_path="/p/a.jpg"),
        ])
        g2 = _make_group([
            _make_record(file_path="/q/c.jpg"),
        ])
        result = resolve_matched_paths([g1, g2], "File Name", r"\.jpg$")
        assert result == ["/p/b.jpg", "/p/a.jpg", "/q/c.jpg"]

    def test_unknown_field_returns_empty(self):
        # _get_record_field returns None for unknown fields; the regex
        # path skips None values so no paths are matched.
        from core.app_service.action_resolve import resolve_matched_paths

        g = _make_group([
            _make_record(file_path="a/x.jpg"),
        ])
        result = resolve_matched_paths([g], "NonExistentField", ".*")
        assert result == []

    def test_malformed_cmp_sentinel_raises(self):
        # A `__cmp__:` PREFIX commits to the threshold branch; a payload that
        # decode_cmp_pattern can't parse RAISES ValueError — it must NOT fall
        # through to plain regex (which could literal-match and mutate a file
        # whose path contains "__cmp__:weird"). Mirrors file_operations.py:1389
        # and is the divergence adversarial review caught; the route maps it
        # to HTTP 422.
        import pytest as _pytest

        from core.app_service.action_resolve import resolve_matched_paths

        g = _make_group([
            _make_record(file_path="__cmp__:weird.jpg"),
        ])
        with _pytest.raises(ValueError):
            resolve_matched_paths([g], "File Name", "__cmp__:weird")

    def test_malformed_top_n_sentinel_raises(self):
        # Same prefix-commit semantics for `__top_n__:` (bad order/count).
        import pytest as _pytest

        from core.app_service.action_resolve import resolve_matched_paths

        g = _make_group([
            _make_record(file_path="__top_n__:abc:desc.jpg"),
        ])
        with _pytest.raises(ValueError):
            resolve_matched_paths([g], "File Name", "__top_n__:abc:desc")
