"""Tests for scanner.exif — date parsing and chunked batch logic."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scanner.exif import parse_exif_date as _parse_exif_date, batch_read_dates, _read_chunk


# ── parse_exif_date ────────────────────────────────────────────────────────

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


# ── ExiftoolProcess (mocked subprocess so tests run without exiftool) ────


class TestExiftoolProcess:
    """Cover ExiftoolProcess by mocking subprocess.Popen.

    These tests exercise the lifecycle without requiring exiftool on PATH —
    important because windows-latest CI runners don't have it installed,
    while a local dev box typically does.
    """

    def _make_mock_proc(self, output_lines: list[str]) -> MagicMock:
        """Build a mock subprocess with stdout/stdin behaviour that
        ExiftoolProcess.execute() expects (one line per readline, ending
        with the {ready} sentinel)."""
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        # Append the {ready} sentinel that ExiftoolProcess looks for.
        lines_iter = iter(output_lines + ["{ready}\n"])
        proc.stdout.readline.side_effect = lambda: next(lines_iter, "")
        return proc

    def test_init_invokes_exiftool_in_stay_open_mode(self, monkeypatch):
        """ExiftoolProcess.__init__ runs subprocess.Popen with -stay_open True."""
        from scanner import exif

        captured: list[list[str]] = []

        def fake_popen(args, **kwargs):
            captured.append(args)
            return self._make_mock_proc([])

        monkeypatch.setattr(exif.subprocess, "Popen", fake_popen)
        exif.ExiftoolProcess()

        assert len(captured) == 1
        assert captured[0][:3] == ["exiftool", "-stay_open", "True"]

    def test_execute_returns_lines_until_ready_sentinel(self, monkeypatch):
        """execute() collects readline output until '{ready}' is hit."""
        from scanner import exif

        proc = self._make_mock_proc(["2024:06:15 10:30:00\n", "-\n", "-\n"])
        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)

        et = exif.ExiftoolProcess()
        out = et.execute(["-DateTimeOriginal", "/fake/img.jpg"])

        # stdin write was called with the args + -execute trailer
        proc.stdin.write.assert_called()
        write_arg = proc.stdin.write.call_args[0][0]
        assert "-execute" in write_arg
        assert "/fake/img.jpg" in write_arg

        # Output is the joined readline lines (sans the {ready} sentinel)
        assert "2024:06:15 10:30:00" in out
        assert "{ready}" not in out

    def test_execute_stops_on_empty_readline(self, monkeypatch):
        """If readline returns '' (EOF), execute breaks out of the loop."""
        from scanner import exif

        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        # Readline returns one line then EOF — no {ready} sentinel.
        proc.stdout.readline.side_effect = ["line1\n", ""]

        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)
        et = exif.ExiftoolProcess()
        out = et.execute(["-DateTimeOriginal"])
        assert "line1" in out

    def test_close_sends_stay_open_false(self, monkeypatch):
        """close() writes '-stay_open\nFalse\n' and waits for the process."""
        from scanner import exif

        proc = self._make_mock_proc([])
        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)
        et = exif.ExiftoolProcess()

        et.close()

        # Last write should request stay_open=False
        all_writes = [c[0][0] for c in proc.stdin.write.call_args_list]
        assert any("-stay_open" in w and "False" in w for w in all_writes)
        proc.wait.assert_called_once()

    def test_close_kills_on_wait_failure(self, monkeypatch):
        """If wait() raises (e.g. timeout), close() falls back to kill."""
        from scanner import exif

        proc = self._make_mock_proc([])
        proc.wait.side_effect = Exception("synthetic wait failure")
        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)
        et = exif.ExiftoolProcess()

        et.close()  # must not raise
        proc.kill.assert_called_once()

    def test_context_manager_calls_close_on_exit(self, monkeypatch):
        from scanner import exif

        proc = self._make_mock_proc([])
        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)

        with exif.ExiftoolProcess() as et:
            assert isinstance(et, exif.ExiftoolProcess)

        # close() writes the stay_open=False signal on exit
        all_writes = [c[0][0] for c in proc.stdin.write.call_args_list]
        assert any("-stay_open" in w and "False" in w for w in all_writes)


# ── _read_chunk short-output guard ───────────────────────────────────────


class TestReadChunkShortOutput:
    """If exiftool returns fewer lines than expected (e.g. crashed mid-batch),
    each path past the available output gets None (covers lines 115-116)."""

    def test_short_output_yields_none_for_remaining_paths(self):
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/img{i}.jpg") for i in range(3)]
        # Only enough output for the first path (3 lines).
        et = MagicMock()
        et.execute.return_value = "\n".join(["2024:06:15 10:30:00", "-", "-"])

        result = _read_chunk(paths, et)
        # The first path resolves; the remaining two get None (short output).
        assert result[paths[0]] is not None
        assert result[paths[1]] is None
        assert result[paths[2]] is None
