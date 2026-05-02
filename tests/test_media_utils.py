"""Tests for app/views/media_utils — small pure-function helpers.

Targets the previously-omitted module so it now contributes to per-file
coverage rather than hiding from the report.
"""

from __future__ import annotations

import pytest

from app.views.media_utils import (
    VIDEO_EXTENSIONS,
    format_duration,
    is_video,
    normalize_windows_path,
)


class TestIsVideo:
    @pytest.mark.parametrize("path", [
        "/some/clip.mp4",
        r"C:\videos\clip.MP4",   # case-insensitive
        "movie.mov",
        "screen.webm",
        "archive.avi",
        "anime.mkv",
    ])
    def test_recognized_video_extensions(self, path):
        assert is_video(path) is True

    @pytest.mark.parametrize("path", [
        "photo.jpg",
        "photo.heic",
        "photo.png",
        "no_extension",
        "",
        "config.toml",
    ])
    def test_non_video_extensions(self, path):
        assert is_video(path) is False

    def test_video_extensions_set_intact(self):
        """Catch accidental edits to the canonical extension set."""
        assert ".mp4" in VIDEO_EXTENSIONS
        assert ".mov" in VIDEO_EXTENSIONS
        assert ".jpg" not in VIDEO_EXTENSIONS


class TestFormatDuration:
    def test_zero_ms(self):
        assert format_duration(0) == "00:00"

    def test_seconds_only(self):
        assert format_duration(45_000) == "00:45"

    def test_minutes_and_seconds(self):
        assert format_duration(3 * 60_000 + 7_000) == "03:07"

    def test_hours_format(self):
        # 1h 23m 45s
        assert format_duration(3600_000 + 23 * 60_000 + 45_000) == "01:23:45"

    def test_negative_returns_placeholder(self):
        assert format_duration(-1) == "--:--"
        assert format_duration(-1000) == "--:--"

    def test_sub_second_truncates(self):
        assert format_duration(999) == "00:00"  # < 1s


class TestNormalizeWindowsPath:
    def test_forward_slashes_become_backslashes(self):
        assert normalize_windows_path("C:/foo/bar") == r"C:\foo\bar"

    def test_drive_letter_upcased(self):
        assert normalize_windows_path("c:\\foo") == r"C:\foo"
        assert normalize_windows_path("d:/photos/img.jpg") == r"D:\photos\img.jpg"

    def test_normalizes_dotdot(self):
        assert normalize_windows_path("C:/foo/../bar") == r"C:\bar"

    def test_no_drive_letter_still_normalizes(self):
        assert normalize_windows_path("foo/bar/../baz") == r"foo\baz"

    def test_falls_back_to_input_on_exception(self, monkeypatch):
        """Defensive: if os.path.normpath ever raises, return the input unchanged."""
        import os as os_module

        def raising(_):
            raise ValueError("boom")

        monkeypatch.setattr(os_module.path, "normpath", raising)
        assert normalize_windows_path("anything") == "anything"
