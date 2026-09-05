"""Anti-drift pin: core.app_service.action_resolve must be byte-identical
to the Qt originals in app.views.dialogs.select_dialog.

This file imports BOTH the Qt source and the Qt-free copy and asserts
identical output over a shared fixture of duck-typed groups/records.

If the Qt original is updated (new operator, new field, encode format
change) without updating the copy, these tests will fail — that is the
intended behaviour.

Note: importing select_dialog pulls PySide6.  That is fine — the
test suite already imports it in test_select_dialog.py which CI runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _f
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# Duck-typed fixture helpers (no Qt)
# ---------------------------------------------------------------------------

def _make_record(
    *, file_path: str, file_size_bytes: int = 0,
    score: float | None = None, hamming_distance: int | None = None,
    creation_date=None, shot_date=None, is_locked: bool = False,
):
    @dataclass
    class _Rec:
        file_path: str
        file_size_bytes: int
        score: float | None
        hamming_distance: int | None
        creation_date: object
        shot_date: object
        is_locked: bool

    return _Rec(
        file_path=file_path,
        file_size_bytes=file_size_bytes,
        score=score,
        hamming_distance=hamming_distance,
        creation_date=creation_date,
        shot_date=shot_date,
        is_locked=is_locked,
    )


def _make_group(items):
    @dataclass
    class _G:
        items: list = _f(default_factory=list)

    return _G(items=list(items))


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mixed_groups():
    """Two groups with varied scores, sizes, and dates."""
    g1 = _make_group([
        _make_record(file_path="g1/a.jpg", score=0.9, file_size_bytes=200,
                     creation_date=datetime(2026, 6, 1)),
        _make_record(file_path="g1/b.jpg", score=0.3, file_size_bytes=50,
                     creation_date=datetime(2020, 1, 1)),
        _make_record(file_path="g1/c.mov", score=None, file_size_bytes=100),
    ])
    g2 = _make_group([
        _make_record(file_path="g2/x.jpg", score=0.5, file_size_bytes=80),
        _make_record(file_path="g2/y.jpg", score=0.5, file_size_bytes=120),
    ])
    return [g1, g2]


# ---------------------------------------------------------------------------
# select_paths_by_threshold parity
# ---------------------------------------------------------------------------

class TestThresholdParity:
    """select_paths_by_threshold output is identical between Qt and copy."""

    def test_score_gt(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Score", ">", "0.4") == \
               qt_fn(mixed_groups, "Score", ">", "0.4")

    def test_score_ge(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Score", ">=", "0.5") == \
               qt_fn(mixed_groups, "Score", ">=", "0.5")

    def test_size_lt(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Size (Bytes)", "<", "100") == \
               qt_fn(mixed_groups, "Size (Bytes)", "<", "100")

    def test_size_ne(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Size (Bytes)", "!=", "100") == \
               qt_fn(mixed_groups, "Size (Bytes)", "!=", "100")

    def test_size_le(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Size (Bytes)", "<=", "100") == \
               qt_fn(mixed_groups, "Size (Bytes)", "<=", "100")

    def test_score_eq(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Score", "==", "0.5") == \
               qt_fn(mixed_groups, "Score", "==", "0.5")

    def test_creation_date_gt_isodate(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Creation Date", ">", "2024-01-01") == \
               qt_fn(mixed_groups, "Creation Date", ">", "2024-01-01")

    def test_none_score_skipped_in_both(self, qapp, mixed_groups):
        # Verifies that score=None (the MOV passenger in g1) is excluded
        # identically by both implementations.
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Score", ">", "0") == \
               qt_fn(mixed_groups, "Score", ">", "0")

    def test_unparseable_threshold_both_return_empty(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_by_threshold as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_by_threshold as copy_fn,
        )
        assert copy_fn(mixed_groups, "Size (Bytes)", ">", "xyz") == \
               qt_fn(mixed_groups, "Size (Bytes)", ">", "xyz") == []


# ---------------------------------------------------------------------------
# select_paths_top_n parity
# ---------------------------------------------------------------------------

class TestTopNParity:
    """select_paths_top_n output is identical between Qt and copy."""

    def test_top_1_score_desc(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_top_n as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_top_n as copy_fn,
        )
        assert copy_fn(mixed_groups, "Score", 1, "desc") == \
               qt_fn(mixed_groups, "Score", 1, "desc")

    def test_top_1_score_asc(self, qapp, mixed_groups):
        from app.views.dialogs.select_dialog import (
            select_paths_top_n as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_top_n as copy_fn,
        )
        assert copy_fn(mixed_groups, "Score", 1, "asc") == \
               qt_fn(mixed_groups, "Score", 1, "asc")

    def test_top_n_tie_breaks_identically(self, qapp):
        # g2 has two records with equal score=0.5; tiebreaker must be
        # the same (file_path ascending) in both implementations.
        from app.views.dialogs.select_dialog import (
            select_paths_top_n as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_top_n as copy_fn,
        )
        g = _make_group([
            _make_record(file_path="g2/y.jpg", score=0.5),
            _make_record(file_path="g2/x.jpg", score=0.5),
        ])
        assert copy_fn([g], "Score", 1, "desc") == \
               qt_fn([g], "Score", 1, "desc")

    def test_none_excluded_identically(self, qapp, mixed_groups):
        # MOV passenger in g1 has score=None; both must exclude it.
        from app.views.dialogs.select_dialog import (
            select_paths_top_n as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_top_n as copy_fn,
        )
        assert copy_fn(mixed_groups, "Score", 5, "desc") == \
               qt_fn(mixed_groups, "Score", 5, "desc")

    def test_n_larger_than_group_identically(self, qapp):
        from app.views.dialogs.select_dialog import (
            select_paths_top_n as qt_fn,
        )
        from core.app_service.action_resolve import (
            select_paths_top_n as copy_fn,
        )
        g = _make_group([
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.9),
        ])
        assert copy_fn([g], "Score", 99, "asc") == \
               qt_fn([g], "Score", 99, "asc")


# ---------------------------------------------------------------------------
# decode_cmp_pattern + decode_top_n_pattern parity
# ---------------------------------------------------------------------------

class TestDecodeParity:
    """encode/decode output is identical between Qt and copy."""

    @pytest.mark.parametrize("op,value", [
        (">", "0.5"),
        (">=", "100"),
        ("<", "2026-01-01"),
        ("!=", "0"),
        ("==", "1.0"),
    ])
    def test_decode_cmp_pattern(self, qapp, op, value):
        from app.views.dialogs.select_dialog import (
            decode_cmp_pattern as qt_dec,
            encode_cmp_pattern as qt_enc,
        )
        from core.app_service.action_resolve import (
            decode_cmp_pattern as copy_dec,
            encode_cmp_pattern as copy_enc,
        )
        encoded = qt_enc(op, value)
        assert copy_dec(encoded) == qt_dec(encoded)
        # Also verify cross-encode is transparent.
        assert qt_dec(copy_enc(op, value)) == (op, value)

    @pytest.mark.parametrize("n,order", [
        (1, "desc"),
        (3, "asc"),
        (10, "desc"),
    ])
    def test_decode_top_n_pattern(self, qapp, n, order):
        from app.views.dialogs.select_dialog import (
            decode_top_n_pattern as qt_dec,
            encode_top_n_pattern as qt_enc,
        )
        from core.app_service.action_resolve import (
            decode_top_n_pattern as copy_dec,
            encode_top_n_pattern as copy_enc,
        )
        encoded = qt_enc(n, order)
        assert copy_dec(encoded) == qt_dec(encoded)
        assert qt_dec(copy_enc(n, order)) == (n, order)

    def test_decode_cmp_non_prefix_returns_none_in_both(self, qapp):
        from app.views.dialogs.select_dialog import (
            decode_cmp_pattern as qt_fn,
        )
        from core.app_service.action_resolve import (
            decode_cmp_pattern as copy_fn,
        )
        for pattern in ("IMG.*", "", "plain_regex", "__top_n__:1:desc"):
            assert copy_fn(pattern) == qt_fn(pattern) is None

    def test_decode_top_n_bad_order_returns_none_in_both(self, qapp):
        from app.views.dialogs.select_dialog import (
            decode_top_n_pattern as qt_fn,
        )
        from core.app_service.action_resolve import (
            decode_top_n_pattern as copy_fn,
        )
        assert copy_fn("__top_n__:3:garbage") == qt_fn("__top_n__:3:garbage") is None


# ---------------------------------------------------------------------------
# _get_record_field parity — the plain-regex field-extraction path
# ---------------------------------------------------------------------------
# This is the half of resolve_matched_paths that adversarial review flagged
# as unpinned (peerA): the regex branch reads each record's field via
# _get_record_field, whose Qt twin lives in file_operations.py. A drift here
# (e.g. the Lock "Locked"/"" rendering, the Resolution U+00D7 glyph, or the
# File Name basename extraction) would silently change which rows a regex
# matches. The dispatcher's CONTROL flow (startswith→decode→raise, else regex)
# is now byte-identical to file_operations.py:1387-1408 and is pinned
# behaviourally in test_action_resolve.py; the Qt dispatcher itself is a
# FileOperationsHandler METHOD (needs a Qt handler instance) so it is not
# standalone-callable — its components (_get_record_field + the three leaf
# selectors) are cross-checked here and above instead.


def _make_rich_record():
    @dataclass
    class _RichRec:
        file_path: str = "C:/photos/2024/IMG_0042.JPG"
        folder_path: str = "C:/photos/2024"
        user_decision: str = "delete"
        is_locked: bool = True
        file_size_bytes: int = 1234
        creation_date: object = None
        shot_date: object = None
        score: float | None = 0.87
        pixel_width: int | None = 1920
        pixel_height: int | None = 1080

    return _RichRec()


class TestFieldExtractionParity:
    """_get_record_field output is identical between Qt and copy."""

    @pytest.mark.parametrize("field", [
        "File Name", "Folder", "Action", "Lock", "Size (Bytes)",
        "Score", "Resolution", "NonExistentField",
    ])
    def test_field_extraction_matches(self, qapp, field):
        from app.views.handlers.file_operations import _get_record_field as qt_fn
        from core.app_service.action_resolve import _get_record_field as copy_fn

        rec = _make_rich_record()
        assert copy_fn(rec, field) == qt_fn(rec, field)

    def test_lock_unlocked_renders_empty_in_both(self, qapp):
        from app.views.handlers.file_operations import _get_record_field as qt_fn
        from core.app_service.action_resolve import _get_record_field as copy_fn

        rec = _make_rich_record()
        rec.is_locked = False
        # Both must render the unlocked Lock field as "" (not "None"/"False").
        assert copy_fn(rec, "Lock") == qt_fn(rec, "Lock") == ""

    def test_resolution_missing_dim_is_none_in_both(self, qapp):
        from app.views.handlers.file_operations import _get_record_field as qt_fn
        from core.app_service.action_resolve import _get_record_field as copy_fn

        rec = _make_rich_record()
        rec.pixel_height = None
        assert copy_fn(rec, "Resolution") == qt_fn(rec, "Resolution") is None
