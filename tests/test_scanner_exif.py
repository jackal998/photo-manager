"""Tests for scanner.exif — date parsing and chunked batch logic."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scanner.exif import _parse_exif_date, batch_read_dates, _read_chunk


# ── _parse_exif_date ───────────────────────────────────────────────────────

class TestParseExifDate:
    def test_valid_date(self):
        result = _parse_exif_date("2024:06:15 10:30:00")
        assert result == datetime(2024, 6, 15, 10, 30, 0)

    def test_empty_string(self):
        assert _parse_exif_date("") is None

    def test_dash_sentinel(self):
        assert _parse_exif_date("-") is None

    def test_zero_date(self):
        assert _parse_exif_date("0000:00:00 00:00:00") is None

    def test_whitespace_stripped(self):
        result = _parse_exif_date("  2023:01:01 00:00:00  ")
        assert result == datetime(2023, 1, 1, 0, 0, 0)

    def test_with_timezone_suffix(self):
        """Timezone suffix should be stripped, not cause a parse error."""
        result = _parse_exif_date("2024:06:01 12:00:00+09:00")
        assert result == datetime(2024, 6, 1, 12, 0, 0)

    def test_invalid_format_returns_none(self):
        assert _parse_exif_date("not-a-date") is None


# ── batch_read_dates ───────────────────────────────────────────────────────

def _make_mock_et(lines_per_file: list[tuple[str, str, str]]) -> MagicMock:
    """Build a mock ExiftoolProcess that returns prepared output lines."""
    responses = []
    for dto, create, qt_create in lines_per_file:
        responses.extend([dto, create, qt_create])

    et = MagicMock()
    et.execute.return_value = "\n".join(responses)
    return et


class TestBatchReadDates:
    def test_empty_paths_returns_empty(self):
        et = MagicMock()
        result = batch_read_dates([], et)
        assert result == {}
        et.execute.assert_not_called()

    def test_single_file_with_date(self):
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([("2024:06:15 10:30:00", "-", "-")])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] == datetime(2024, 6, 15, 10, 30, 0)

    def test_falls_back_to_create_date(self):
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([("-", "2024:01:01 08:00:00", "-")])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 8, 0, 0)

    def test_falls_back_to_quicktime_create_date(self):
        paths = [Path("/fake/vid.mov")]
        et = _make_mock_et([("-", "-", "2023:12:25 18:00:00")])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] == datetime(2023, 12, 25, 18, 0, 0)

    def test_no_date_returns_none(self):
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([("-", "-", "-")])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] is None

    def test_chunking_calls_execute_multiple_times(self):
        """With chunk_size=2 and 5 files, execute should be called 3 times."""
        paths = [Path(f"/fake/{i}.jpg") for i in range(5)]
        responses = [("-", "-", "-")] * 5

        call_count = [0]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            call_count[0] += 1
            # Return 3 lines per file in this chunk
            return "\n".join(["-", "-", "-"] * len(chunk_paths))

        et = MagicMock()
        et.execute.side_effect = fake_execute

        batch_read_dates(paths, et, chunk_size=2)
        assert et.execute.call_count == 3  # ceil(5/2)

    def test_chunking_returns_all_paths(self):
        """All input paths should appear as keys in the result."""
        paths = [Path(f"/fake/{i}.jpg") for i in range(7)]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            return "\n".join(["-", "-", "-"] * len(chunk_paths))

        et = MagicMock()
        et.execute.side_effect = fake_execute

        result = batch_read_dates(paths, et, chunk_size=3)
        assert set(result.keys()) == set(paths)
