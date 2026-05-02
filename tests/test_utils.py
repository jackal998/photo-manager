"""Tests for infrastructure.utils date parsing/formatting utilities."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from infrastructure.utils import (
    get_exif_datetime_original,
    get_filesystem_creation_datetime,
)


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

    def test_falls_back_to_datetime_tag_306_when_36867_absent(self, tmp_path):
        """When DateTimeOriginal (36867) is missing but DateTime (306) is present, use 306."""
        f = tmp_path / "datetime_only.jpg"
        img = Image.new("RGB", (10, 10), color=(200, 100, 50))
        exif = img.getexif()
        exif[306] = "2024:02:14 09:30:00"   # DateTime, not DateTimeOriginal
        img.save(str(f), "JPEG", exif=exif.tobytes())

        result = get_exif_datetime_original(str(f))
        assert result == datetime(2024, 2, 14, 9, 30, 0)

    def test_returns_none_when_exif_value_is_empty_string(self, tmp_path):
        """An EXIF tag set to '' should not crash; return None gracefully."""
        f = tmp_path / "empty_exif.jpg"
        img = Image.new("RGB", (10, 10), color=(200, 100, 50))
        exif = img.getexif()
        exif[36867] = ""   # explicitly empty — `if not val` branch
        img.save(str(f), "JPEG", exif=exif.tobytes())

        result = get_exif_datetime_original(str(f))
        assert result is None

    def test_returns_none_when_exif_value_is_unparseable(self, tmp_path):
        """An EXIF date in a totally unparseable format → None, not exception."""
        f = tmp_path / "garbled.jpg"
        img = Image.new("RGB", (10, 10), color=(200, 100, 50))
        exif = img.getexif()
        # Not the canonical "YYYY:MM:DD HH:MM:SS"; not a valid ISO format either
        exif[36867] = "some garbage string"
        img.save(str(f), "JPEG", exif=exif.tobytes())

        result = get_exif_datetime_original(str(f))
        assert result is None

    def test_parses_iso_format_when_canonical_format_does_not_match(self, tmp_path):
        """A non-EXIF date string that ISO-parses (after / and . normalization) is accepted.

        The fallback `datetime.fromisoformat(val_str.replace("/", "-").replace(".", ":"))`
        path covers files where some other tool wrote a non-canonical date.
        """
        f = tmp_path / "iso_format.jpg"
        img = Image.new("RGB", (10, 10), color=(200, 100, 50))
        exif = img.getexif()
        # 19 chars but with `-` separators in date portion → fails the
        # `val_str[4] == ":"` check, so falls through to fromisoformat.
        exif[36867] = "2025-03-21T11:22:33"
        img.save(str(f), "JPEG", exif=exif.tobytes())

        result = get_exif_datetime_original(str(f))
        assert result == datetime(2025, 3, 21, 11, 22, 33)
