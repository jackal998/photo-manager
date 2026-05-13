"""Tests for core.services.sort_service.SortService."""

from __future__ import annotations

from core.models import PhotoGroup, PhotoRecord
from core.services.sort_service import SortService


def _rec(
    path: str,
    size: int = 0,
    folder: str = "",
    score: float | None = 0.0,
) -> PhotoRecord:
    return PhotoRecord(
        group_number=1,
        is_mark=False,
        is_locked=False,
        folder_path=folder,
        file_path=path,
        capture_date=None,
        modified_date=None,
        file_size_bytes=size,
        score=score,
    )


def _group(*recs: PhotoRecord) -> PhotoGroup:
    return PhotoGroup(group_number=1, items=list(recs))


class TestSortService:
    def test_single_key_ascending(self):
        g = _group(_rec("/b.jpg", size=200), _rec("/a.jpg", size=100))
        SortService().sort([g], [("file_size_bytes", True)])
        assert [r.file_size_bytes for r in g.items] == [100, 200]

    def test_single_key_descending(self):
        g = _group(_rec("/a.jpg", size=100), _rec("/b.jpg", size=200))
        SortService().sort([g], [("file_size_bytes", False)])
        assert [r.file_size_bytes for r in g.items] == [200, 100]

    def test_multi_key_stable(self):
        # primary: size desc; tiebreak: path asc
        g = _group(
            _rec("/c.jpg", size=100),
            _rec("/a.jpg", size=200),
            _rec("/b.jpg", size=100),
        )
        SortService().sort([g], [("file_size_bytes", False), ("file_path", True)])
        paths = [r.file_path for r in g.items]
        assert paths == ["/a.jpg", "/b.jpg", "/c.jpg"]

    def test_none_value_treated_as_empty_string(self):
        # None folder_path coerces to "" → sorts before any real folder string
        g = _group(_rec("/b.jpg", folder="Z"), _rec("/a.jpg", folder=None))
        SortService().sort([g], [("folder_path", True)])
        assert g.items[0].file_path == "/a.jpg"  # None/"" sorts first

    def test_empty_sort_keys_noop(self):
        recs = [_rec("/b.jpg"), _rec("/a.jpg")]
        g = _group(*recs)
        SortService().sort([g], [])
        assert [r.file_path for r in g.items] == ["/b.jpg", "/a.jpg"]

    def test_updates_group_items(self):
        g = _group(_rec("/b.jpg", size=2), _rec("/a.jpg", size=1))
        SortService().sort([g], [("file_size_bytes", True)])
        assert [r.file_size_bytes for r in g.items] == [1, 2]

    def test_multiple_groups_independent(self):
        g1 = _group(_rec("/b.jpg", size=2), _rec("/a.jpg", size=1))
        g2 = _group(_rec("/d.jpg", size=4), _rec("/c.jpg", size=3))
        SortService().sort([g1, g2], [("file_size_bytes", True)])
        assert [r.file_size_bytes for r in g1.items] == [1, 2]
        assert [r.file_size_bytes for r in g2.items] == [3, 4]

    def test_none_on_numeric_field_substitutes_zero(self):
        """Previously this case raised TypeError: a numeric field with
        mixed None / float values would build sort tuples of
        ``(-0.8,)`` vs ``((1, ""),)`` and Python 3 refuses to order
        float against tuple. The regression-test contract from #187 PR 5:
        None substitutes the type-appropriate zero so the sort works
        end-to-end."""
        # ``score`` is float|None on PhotoRecord — exercise it directly.
        g = _group(
            _rec("/a.jpg", score=0.8),
            _rec("/b.jpg", score=None),
            _rec("/c.jpg", score=0.5),
        )
        SortService().sort([g], [("score", False)])  # descending
        paths = [r.file_path for r in g.items]
        # 0.8 first, 0.5 next, None (substituted to 0) last under desc.
        assert paths == ["/a.jpg", "/c.jpg", "/b.jpg"]

    def test_all_none_on_numeric_field_does_not_crash(self):
        """If every item has None for the sort field (e.g. an old manifest
        loaded before scoring was wired in), sort must still be
        deterministic — not raise."""
        g = _group(
            _rec("/a.jpg", score=None),
            _rec("/b.jpg", score=None),
        )
        SortService().sort([g], [("score", False)])
        # No assertion on order — just verify no exception was raised
        # and the items are still present.
        assert len(g.items) == 2
