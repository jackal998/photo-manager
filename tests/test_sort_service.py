"""Tests for core.services.sort_service.SortService."""

from __future__ import annotations

from core.models import PhotoGroup, PhotoRecord
from core.services.sort_service import SortService


def _rec(path: str, size: int = 0, folder: str = "") -> PhotoRecord:
    return PhotoRecord(
        group_number=1,
        is_mark=False,
        is_locked=False,
        folder_path=folder,
        file_path=path,
        capture_date=None,
        modified_date=None,
        file_size_bytes=size,
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
