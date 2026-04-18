"""Tests for infrastructure.csv_repository.CsvPhotoRepository."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pytest

from core.models import PhotoGroup, PhotoRecord
from infrastructure.csv_repository import CsvPhotoRepository, CSV_HEADERS

# ── helpers ────────────────────────────────────────────────────────────────

def _make_record(tmp_path, group=1, is_mark=False, is_locked=False, name="a.jpg"):
    """Create a real tiny JPEG and return a PhotoRecord pointing to it."""
    from PIL import Image
    f = tmp_path / name
    Image.new("RGB", (4, 4)).save(str(f), "JPEG")
    return PhotoRecord(
        group_number=group,
        is_mark=is_mark,
        is_locked=is_locked,
        folder_path=str(tmp_path),
        file_path=str(f),
        capture_date=datetime(2024, 1, 1),
        modified_date=datetime(2024, 1, 2),
        file_size_bytes=f.stat().st_size,
    )


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


# ── load ───────────────────────────────────────────────────────────────────

class TestLoad:
    def test_basic_round_trip(self, tmp_path):
        """Save a group then reload it — core fields survive."""
        rec = _make_record(tmp_path)
        group = PhotoGroup(group_number=1, items=[rec])
        repo = CsvPhotoRepository()

        out = tmp_path / "out.csv"
        repo.save(str(out), [group])
        records = list(repo.load(str(out)))

        assert len(records) == 1
        assert records[0].file_path == rec.file_path
        assert records[0].group_number == 1

    def test_is_mark_round_trip(self, tmp_path):
        rec = _make_record(tmp_path, is_mark=True)
        group = PhotoGroup(group_number=1, items=[rec])
        repo = CsvPhotoRepository()
        out = tmp_path / "out.csv"
        repo.save(str(out), [group])
        loaded = list(repo.load(str(out)))
        assert loaded[0].is_mark is True

    def test_is_locked_round_trip(self, tmp_path):
        rec = _make_record(tmp_path, is_locked=True)
        group = PhotoGroup(group_number=1, items=[rec])
        repo = CsvPhotoRepository()
        out = tmp_path / "out.csv"
        repo.save(str(out), [group])
        loaded = list(repo.load(str(out)))
        assert loaded[0].is_locked is True

    def test_missing_required_header_raises(self, tmp_path):
        bad = tmp_path / "bad.csv"
        with bad.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["GroupNumber", "FilePath"])
            writer.writeheader()
            writer.writerow({"GroupNumber": "1", "FilePath": "/a/b.jpg"})
        with pytest.raises(ValueError, match="missing required headers"):
            list(CsvPhotoRepository().load(str(bad)))

    def test_corrupt_row_skipped(self, tmp_path):
        """A row with a non-integer GroupNumber should be skipped, not crash."""
        rec = _make_record(tmp_path)
        group = PhotoGroup(group_number=1, items=[rec])
        repo = CsvPhotoRepository()
        out = tmp_path / "out.csv"
        repo.save(str(out), [group])

        # Inject a corrupt row
        rows = out.read_text(encoding="utf-8").splitlines()
        rows.insert(2, "BAD,0,0,/x,/x/y.jpg,,,,,0")
        out.write_text("\n".join(rows), encoding="utf-8")

        # Should silently skip the bad row
        loaded = list(repo.load(str(out)))
        assert any(r.file_path == rec.file_path for r in loaded)

    def test_bool_encoded_as_zero_one(self, tmp_path):
        """IsMark/IsLocked encoded as 0/1 should load correctly."""
        rec_file = tmp_path / "img.jpg"
        from PIL import Image
        Image.new("RGB", (4, 4)).save(str(rec_file), "JPEG")
        row = {
            "GroupNumber": "2",
            "IsMark": "1",
            "IsLocked": "0",
            "FolderPath": str(tmp_path),
            "FilePath": str(rec_file),
            "Capture Date": "2024-03-01 08:00:00",
            "Modified Date": "2024-03-01 08:00:00",
            "Creation Date": "",
            "Shot Date": "",
            "FileSize": "0",
        }
        out = tmp_path / "out.csv"
        _write_csv(out, [row])
        loaded = list(CsvPhotoRepository().load(str(out)))
        assert loaded[0].is_mark is True
        assert loaded[0].is_locked is False


# ── save ───────────────────────────────────────────────────────────────────

class TestSave:
    def test_creates_parent_directories(self, tmp_path):
        rec = _make_record(tmp_path)
        group = PhotoGroup(group_number=1, items=[rec])
        out = tmp_path / "sub" / "dir" / "out.csv"
        CsvPhotoRepository().save(str(out), [group])
        assert out.exists()

    def test_file_size_overwritten_from_disk(self, tmp_path):
        """FileSize column must reflect actual file size, not the stored value."""
        rec = _make_record(tmp_path)
        rec.file_size_bytes = 999_999  # wrong value on purpose
        actual_size = Path(rec.file_path).stat().st_size
        group = PhotoGroup(group_number=1, items=[rec])
        out = tmp_path / "out.csv"
        CsvPhotoRepository().save(str(out), [group])

        with out.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert int(row["FileSize"]) == actual_size

    def test_multiple_groups_saved(self, tmp_path):
        r1 = _make_record(tmp_path, group=1, name="a.jpg")
        r2 = _make_record(tmp_path, group=2, name="b.jpg")
        groups = [
            PhotoGroup(group_number=1, items=[r1]),
            PhotoGroup(group_number=2, items=[r2]),
        ]
        out = tmp_path / "out.csv"
        CsvPhotoRepository().save(str(out), groups)
        loaded = list(CsvPhotoRepository().load(str(out)))
        assert len(loaded) == 2
        nums = {r.group_number for r in loaded}
        assert nums == {1, 2}
