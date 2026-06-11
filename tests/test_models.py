"""Layer-1 tests for core.models — slots-on-dataclass hygiene (#616).

The dataclasses are converted to @dataclass(slots=True) to drop the
per-instance __dict__. At N=1M records the savings are ~200-400 MB
on CPython 3.11. The new constraint: attribute assignment outside the
declared fields raises AttributeError instead of silently creating a
new entry — these tests pin that contract so a future "forgot to
declare a field" bug surfaces here, not under a user load.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from core.models import PhotoGroup, PhotoRecord


def _rec(**overrides) -> PhotoRecord:
    """Minimal PhotoRecord for tests — only the required fields."""
    base = dict(
        group_number=1,
        is_mark=False,
        is_locked=False,
        folder_path="/dir",
        file_path="/dir/a.jpg",
        capture_date=None,
        modified_date=None,
        file_size_bytes=0,
    )
    base.update(overrides)
    return PhotoRecord(**base)


class TestSlotsContract:
    def test_photo_record_has_slots(self):
        """slots=True must set __slots__ and remove __dict__ from the class."""
        assert hasattr(PhotoRecord, "__slots__")
        # __dict__ is the per-instance dict that slots removes; the class
        # itself still has a mappingproxy __dict__ — instances are what matter.
        rec = _rec()
        assert not hasattr(rec, "__dict__")

    def test_photo_group_has_slots(self):
        assert hasattr(PhotoGroup, "__slots__")
        grp = PhotoGroup(group_number=1)
        assert not hasattr(grp, "__dict__")

    def test_slots_blocks_unknown_attribute_on_record(self):
        """Assigning an undeclared attribute must raise — the whole point
        of slots is to make that error LOUD instead of a silent memory leak."""
        rec = _rec()
        with pytest.raises(AttributeError):
            rec.some_undeclared_field = "value"  # type: ignore[attr-defined]

    def test_slots_blocks_unknown_attribute_on_group(self):
        grp = PhotoGroup(group_number=1)
        with pytest.raises(AttributeError):
            grp.some_undeclared_field = "value"  # type: ignore[attr-defined]


class TestDeclaredFieldWrites:
    """Real production paths mutate these fields post-construction
    (set_decision, set_locked_state, etc.). Pin that every one of them
    still works after slots — this is the regression net the production
    code relies on."""

    def test_user_decision_write_round_trip(self):
        rec = _rec()
        assert rec.user_decision == ""
        rec.user_decision = "delete"
        assert rec.user_decision == "delete"

    def test_is_locked_write_round_trip(self):
        rec = _rec()
        assert rec.is_locked is False
        rec.is_locked = True
        assert rec.is_locked is True

    def test_is_mark_write_round_trip(self):
        rec = _rec()
        assert rec.is_mark is False
        rec.is_mark = True
        assert rec.is_mark is True

    def test_action_write_round_trip(self):
        rec = _rec()
        assert rec.action == ""
        rec.action = "KEEP"
        assert rec.action == "KEEP"

    def test_score_write_round_trip(self):
        """scanner/scoring.py writes .score post-construction (#187)."""
        rec = _rec()
        assert rec.score is None
        rec.score = 0.9
        assert rec.score == 0.9

    def test_folder_path_write_round_trip(self):
        """tests/test_file_operations.py test helper writes folder_path."""
        rec = _rec()
        rec.folder_path = "/other"
        assert rec.folder_path == "/other"

    def test_capture_date_write_round_trip(self):
        rec = _rec()
        rec.capture_date = datetime(2026, 6, 11)
        assert rec.capture_date == datetime(2026, 6, 11)


class TestPhotoGroupOperations:
    def test_items_reassignment(self):
        """ExecuteActionDialog rebinds group.items after pruning (#584)."""
        grp = PhotoGroup(group_number=1, items=[_rec()])
        new_items = [_rec(file_path="/dir/b.jpg")]
        grp.items = new_items
        assert grp.items is new_items

    def test_is_expanded_write_round_trip(self):
        grp = PhotoGroup(group_number=1)
        assert grp.is_expanded is False
        grp.is_expanded = True
        assert grp.is_expanded is True

    def test_dataclasses_replace_compat(self):
        """ExecuteActionDialog uses dataclasses.replace() to build a
        filtered view (#584). replace() goes through __init__, not __dict__,
        so slots=True doesn't break it — pin that contract."""
        grp = PhotoGroup(group_number=1, items=[_rec()])
        new_items = [_rec(file_path="/dir/b.jpg")]
        grp2 = replace(grp, items=new_items)
        assert grp2.items is new_items
        assert grp.items is not new_items  # original unchanged
