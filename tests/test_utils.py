"""Tests for infrastructure.utils date parsing/formatting utilities."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from infrastructure.utils import (
    format_csv_datetime,
    get_exif_datetime_original,
    get_filesystem_creation_datetime,
    parse_csv_datetime,
)


# ── parse_csv_datetime ─────────────────────────────────────────────────────

class TestParseCsvDatetime:
    def test_valid_format(self):
        dt = parse_csv_datetime("2024-06-15 10:30:00")
        assert dt == datetime(2024, 6, 15, 10, 30, 0)

    def test_none_input(self):
        assert parse_csv_datetime(None) is None

    def test_empty_string(self):
        assert parse_csv_datetime("") is None

    def test_wrong_format(self):
        assert parse_csv_datetime("15/06/2024") is None

    def test_whitespace_padded(self):
        dt = parse_csv_datetime("  2024-01-01 00:00:00  ")
        assert dt == datetime(2024, 1, 1, 0, 0, 0)


# ── format_csv_datetime ────────────────────────────────────────────────────

class TestFormatCsvDatetime:
    def test_formats_datetime(self):
        dt = datetime(2024, 6, 15, 10, 30, 0)
        assert format_csv_datetime(dt) == "2024-06-15 10:30:00"

    def test_none_returns_empty(self):
        assert format_csv_datetime(None) == ""

    def test_round_trip(self):
        original = datetime(2023, 12, 31, 23, 59, 59)
        assert parse_csv_datetime(format_csv_datetime(original)) == original


# ── get_filesystem_creation_datetime ──────────────────────────────────────

class TestGetFilesystemCreationDatetime:
    def test_returns_datetime_for_existing_file(self, tmp_path):
        f = tmp_path / "sample.txt"
        f.write_text("hello")
        result = get_filesystem_creation_datetime(str(f))
        assert isinstance(result, datetime)

    def test_returns_none_for_missing_file(self):
        result = get_filesystem_creation_datetime("/does/not/exist/file.jpg")
        assert result is None


# ── get_exif_datetime_original ─────────────────────────────────────────────

class TestGetExifDatetimeOriginal:
    def test_returns_none_for_plain_jpeg(self, tmp_path):
        """JPEG with no EXIF should return None, not crash."""
        f = tmp_path / "plain.jpg"
        img = Image.new("RGB", (10, 10), color=(200, 100, 50))
        img.save(str(f), "JPEG")
        result = get_exif_datetime_original(str(f))
        assert result is None

    def test_returns_none_for_missing_file(self):
        result = get_exif_datetime_original("/does/not/exist/photo.jpg")
        assert result is None

    def test_returns_datetime_when_exif_present(self, tmp_path):
        """Write a JPEG with embedded EXIF tag 36867 (DateTimeOriginal) and verify extraction."""
        import struct
        f = tmp_path / "exif.jpg"
        img = Image.new("RGB", (10, 10), color=(200, 100, 50))

        # Build minimal EXIF using Pillow's built-in exif support
        exif = img.getexif()
        # Tag 36867 = DateTimeOriginal, tag 306 = DateTime
        exif[36867] = "2023:07:04 14:00:00"
        img.save(str(f), "JPEG", exif=exif.tobytes())

        result = get_exif_datetime_original(str(f))
        assert result == datetime(2023, 7, 4, 14, 0, 0)
