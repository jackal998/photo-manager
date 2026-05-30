"""Tests for scanner.exif — date parsing and JSON-based batch logic.

Coverage scope (post photo-manager#145 JSON migration):

* ``parse_exif_date`` — value-level parsing of EXIF date strings.
* ``_parse_exiftool_json`` — robust extraction of the JSON array from
  ``exiftool -j -G`` output, even when the surrounding text contains
  status messages or stderr noise.
* ``_read_chunk`` — binds JSON records to input paths by ``SourceFile``
  identity (not position), so reordered / missing / extra records cannot
  misalign the parser. The drift bug class is structurally eliminated.
* ``ExiftoolProcess`` — separated stdout/stderr pipes (a daemon thread
  drains stderr) so byte-level interleaving is impossible for any output
  size. ``execute()`` appends captured stderr after stdout for
  backward-compatible text-grep callers.

Static fixtures in ``tests/fixtures/exiftool_outputs/`` snapshot real
exiftool ``-j -G`` output captured against ``qa/sandbox/`` files.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scanner.exif import (
    parse_exif_date as _parse_exif_date,
    batch_read_dates,
    _read_chunk,
    _parse_exiftool_json,
)


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


# ── parse_exif_date edge cases (deeper coverage of date-parsing edges) ─────


class TestParseExifDateEdgeCases:
    """Edge cases beyond the happy path. Dates are critical to this app —
    manifest entries, dedup grouping, sort-by-shot-date all depend on
    these parses being correct or explicitly None."""

    def test_subsecond_resolution_is_truncated(self):
        """exiftool emits sub-second precision for cameras that record it
        (verified live against qa/sandbox/exif-edge/subsecond.jpg →
        '2024:05:02 09:30:00.500'). The parser slices to 19 chars before
        strptime, so the fractional part is silently dropped."""
        result = _parse_exif_date("2024:05:02 09:30:00.500")
        assert result == datetime(2024, 5, 2, 9, 30, 0)

    def test_negative_timezone_offset_stripped(self):
        result = _parse_exif_date("2024:06:01 12:00:00-05:00")
        assert result == datetime(2024, 6, 1, 12, 0, 0)

    def test_zero_offset_suffix_stripped(self):
        result = _parse_exif_date("2024:06:01 12:00:00+00:00")
        assert result == datetime(2024, 6, 1, 12, 0, 0)

    def test_zero_date_with_surrounding_whitespace(self):
        assert _parse_exif_date("  0000:00:00 00:00:00  ") is None

    def test_date_only_no_time_returns_none(self):
        assert _parse_exif_date("2024:06:01") is None

    def test_time_only_no_date_returns_none(self):
        assert _parse_exif_date("12:00:00") is None

    def test_two_digit_year_returns_none(self):
        """Legacy formats sometimes emit '94:06:01 12:00:00'. strptime
        with %Y requires 4 digits and will reject this."""
        assert _parse_exif_date("94:06:01 12:00:00") is None

    def test_far_future_date_parses_fine(self):
        result = _parse_exif_date("2099:12:31 23:59:59")
        assert result == datetime(2099, 12, 31, 23, 59, 59)

    def test_leap_second_returns_none(self):
        """Python datetime rejects ':60' seconds (no leap-second support)."""
        assert _parse_exif_date("2024:06:30 23:59:60") is None

    def test_invalid_month_returns_none(self):
        assert _parse_exif_date("2024:13:01 12:00:00") is None

    def test_invalid_day_returns_none(self):
        assert _parse_exif_date("2024:02:30 12:00:00") is None

    def test_zero_date_with_timezone_suffix_returns_none(self):
        """The zero-date prefix check happens before slicing, so a
        decorated zero sentinel '0000:00:00 00:00:00+09:00' must still
        return None."""
        assert _parse_exif_date("0000:00:00 00:00:00+09:00") is None


# ── _parse_exiftool_json ───────────────────────────────────────────────


class TestParseExiftoolJson:
    """The JSON slicer is responsible for finding the ``[ ... ]`` blob
    in the middle of arbitrary surrounding text (stderr appended by
    ``ExiftoolProcess.execute()``, status messages, leading whitespace,
    etc.). Each test pins one shape that real exiftool / wrapper code
    is known to produce."""

    def test_clean_json_array(self):
        out = '[{"SourceFile": "/a.jpg", "EXIF:DateTimeOriginal": "2024:01:01 12:00:00"}]'
        result = _parse_exiftool_json(out)
        assert result == [{
            "SourceFile": "/a.jpg",
            "EXIF:DateTimeOriginal": "2024:01:01 12:00:00",
        }]

    def test_trailing_status_message_ignored(self):
        """Real exiftool with stderr separated emits the JSON, then the
        ``    N image files read`` summary (now from stderr, appended after
        stdout in execute()). The slicer must ignore everything after the
        last ``]``."""
        out = (
            '[{"SourceFile": "/a.jpg", "EXIF:DateTimeOriginal": "2024:01:01 12:00:00"}]\n'
            '    1 image files read'
        )
        result = _parse_exiftool_json(out)
        assert len(result) == 1
        assert result[0]["EXIF:DateTimeOriginal"] == "2024:01:01 12:00:00"

    def test_leading_status_message_ignored(self):
        """Defensive: warning lines on stderr could end up before the JSON
        in pathological orderings. The slicer finds ``[`` no matter where
        it sits in the output."""
        out = (
            'Warning: some non-fatal warning here\n'
            '[{"SourceFile": "/a.jpg", "EXIF:DateTimeOriginal": "2024:01:01 12:00:00"}]'
        )
        result = _parse_exiftool_json(out)
        assert len(result) == 1

    def test_status_messages_both_sides(self):
        out = (
            'pre noise\n'
            '[{"SourceFile": "/a.jpg"}]\n'
            'post noise: 1 image files read'
        )
        result = _parse_exiftool_json(out)
        assert result == [{"SourceFile": "/a.jpg"}]

    def test_malformed_json_no_closing_bracket_returns_empty(self):
        """If JSON is truncated (no closing ``]``) the slicer's
        ``end <= start`` guard fires before json.loads is even called."""
        out = '[{"SourceFile": "/a.jpg", "EXIF:DateTimeOriginal": broken'
        assert _parse_exiftool_json(out) == []

    def test_malformed_json_with_brackets_returns_empty(self):
        """Has both ``[`` and ``]`` so the slicer reaches json.loads, but
        the content between them is invalid JSON. Caught by the
        ``json.JSONDecodeError`` handler."""
        out = '[{"SourceFile": broken_no_quotes}]'
        assert _parse_exiftool_json(out) == []

    def test_empty_output_returns_empty_list(self):
        assert _parse_exiftool_json("") == []

    def test_no_brackets_returns_empty_list(self):
        assert _parse_exiftool_json("just some text") == []

    def test_non_array_root_returns_empty_list(self):
        """exiftool always emits an array under -j; a bare object would be
        a wrapper bug. Defend against it."""
        out = '{"SourceFile": "/a.jpg"}'
        # Note: { has no [ before it, find returns -1
        assert _parse_exiftool_json(out) == []

    def test_object_inside_array_is_kept(self):
        out = '[{"SourceFile": "/a.jpg"}, {"SourceFile": "/b.jpg"}]'
        result = _parse_exiftool_json(out)
        assert len(result) == 2

    def test_empty_array_is_valid(self):
        """exiftool with zero matching files emits ``[]`` then summary."""
        out = '[]\n    0 image files read'
        assert _parse_exiftool_json(out) == []


# ── _make_mock_et helper + batch_read_dates ────────────────────────────


def _make_mock_et(
    paths_and_dates: list[tuple[Path | str, dict | None]],
    trailing_summary: bool = True,
    extra_stderr: str = "",
) -> MagicMock:
    """Build a mock ExiftoolProcess that returns realistic ``-j -G`` JSON.

    ``paths_and_dates`` is a list of ``(path, record)`` pairs:
    * ``path`` becomes the ``SourceFile`` field.
    * ``record`` is a dict of additional JSON keys for that file (e.g.
      ``{"EXIF:DateTimeOriginal": "2024:..."}``). Pass ``None`` to emit
      a record with only ``SourceFile`` (which is what real exiftool emits
      for files where every queried tag was absent — see the
      ``datetime_tag_only.jpg`` capture in the static fixtures).

    Output mirrors what real exiftool emits (verified live against
    ``qa/sandbox/``):

        [{
          "SourceFile": "<path>",
          ...record fields...
        },
        ...]
            N image files read

    The trailing summary is appended via the ``ExiftoolProcess.execute()``
    stderr-merge mechanism in the real code; here we simulate that by
    appending it directly to the mock return value. Set
    ``trailing_summary=False`` to omit (e.g. for testing what happens when
    exiftool produced no progress message).

    History: this helper used to emit a flat ``3 * N`` line-positional
    shape with no headers, matching the pre-#145 buggy parser's mental
    model. The parser indexed against that shape and the mock provided
    exactly that shape, so tests passed by tautology. The shape was
    revised to include ``========`` headers in the metaline-strip era,
    then again to JSON for the structural fix in google-album-metadata#5.
    Each iteration brought the mock closer to what real exiftool emits.
    """
    import json as _json

    records: list[dict] = []
    for path, rec in paths_and_dates:
        record = {"SourceFile": str(path)}
        if rec:
            record.update(rec)
        records.append(record)

    json_text = _json.dumps(records, indent=2)
    output = json_text
    if trailing_summary:
        output += f"\n    {len(paths_and_dates)} image files read"
    if extra_stderr:
        output += "\n" + extra_stderr

    et = MagicMock()
    et.execute.return_value = output
    return et


class TestBatchReadDates:
    def test_empty_paths_returns_empty(self):
        et = MagicMock()
        result = batch_read_dates([], et)
        assert result == {}
        et.execute.assert_not_called()

    def test_single_file_with_date(self):
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:06:15 10:30:00"}),
        ])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] == datetime(2024, 6, 15, 10, 30, 0)

    def test_falls_back_to_create_date(self):
        """No DateTimeOriginal, but EXIF:CreateDate present (the
        ``createdate_only.jpg`` shape)."""
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:CreateDate": "2024:01:01 08:00:00"}),
        ])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 8, 0, 0)

    def test_falls_back_to_quicktime_create_date(self):
        paths = [Path("/fake/vid.mov")]
        et = _make_mock_et([
            (paths[0], {"QuickTime:CreateDate": "2023:12:25 18:00:00"}),
        ])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] == datetime(2023, 12, 25, 18, 0, 0)

    def test_xmp_datetime_original_used_for_png_etc(self):
        """PNG / GIF / WebP carry the date in XMP rather than EXIF in some
        write pipelines. The fallback chain checks EXIF then XMP for
        DateTimeOriginal."""
        paths = [Path("/fake/img.png")]
        et = _make_mock_et([
            (paths[0], {"XMP:DateTimeOriginal": "2024:03:15 14:00:00"}),
        ])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] == datetime(2024, 3, 15, 14, 0, 0)

    def test_no_date_returns_none(self):
        """Record exists but has no recognized date keys."""
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([(paths[0], None)])
        result = batch_read_dates(paths, et)
        assert result[paths[0]] is None

    def test_chunking_calls_execute_multiple_times(self):
        """With chunk_size=2 and 5 files, execute should be called 3 times."""
        import json as _json

        paths = [Path(f"/fake/{i}.jpg") for i in range(5)]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            records = [{"SourceFile": p} for p in chunk_paths]
            return _json.dumps(records)

        et = MagicMock()
        et.execute.side_effect = fake_execute

        batch_read_dates(paths, et, chunk_size=2)
        assert et.execute.call_count == 3  # ceil(5/2)

    def test_chunking_returns_all_paths(self):
        """All input paths should appear as keys in the result."""
        import json as _json

        paths = [Path(f"/fake/{i}.jpg") for i in range(7)]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            records = [{"SourceFile": p} for p in chunk_paths]
            return _json.dumps(records)

        et = MagicMock()
        et.execute.side_effect = fake_execute

        result = batch_read_dates(paths, et, chunk_size=3)
        assert set(result.keys()) == set(paths)


# ── ExiftoolProcess (mocked subprocess so tests run without exiftool) ──


class TestExiftoolProcess:
    """Cover ExiftoolProcess by mocking subprocess.Popen.

    These tests exercise the lifecycle without requiring exiftool on PATH —
    important because windows-latest CI runners don't have it installed,
    while a local dev box typically does.

    Post photo-manager#145: stderr is now drained on a daemon thread, so
    every mock proc must provide a stderr.readline that returns "" (EOF)
    immediately. Otherwise the drain thread spins on a MagicMock, leaks
    memory, and pollutes other tests.
    """

    def _make_mock_proc(
        self,
        stdout_lines: list[str],
        stderr_lines: list[str] | None = None,
    ) -> MagicMock:
        """Build a mock subprocess with stdout AND stderr behaviour.

        stderr_lines defaults to [] (immediate EOF) so the drain thread
        exits cleanly. Pass a list to simulate exiftool emitting messages
        on stderr (progress reports, warnings).
        """
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()

        # stdout: lines + {ready} sentinel.
        stdout_iter = iter(stdout_lines + ["{ready}\n"])
        proc.stdout.readline.side_effect = lambda: next(stdout_iter, "")

        # stderr: lines + EOF (so drain thread exits).
        if stderr_lines is None:
            stderr_lines = []
        stderr_iter = iter(stderr_lines + [""])
        proc.stderr.readline.side_effect = lambda: next(stderr_iter, "")

        return proc

    def test_init_invokes_exiftool_in_stay_open_mode(self, monkeypatch):
        """ExiftoolProcess.__init__ runs subprocess.Popen with -stay_open True."""
        from scanner import exif

        captured: list[list[str]] = []
        captured_kwargs: list[dict] = []

        def fake_popen(args, **kwargs):
            captured.append(args)
            captured_kwargs.append(kwargs)
            return self._make_mock_proc([])

        monkeypatch.setattr(exif.subprocess, "Popen", fake_popen)
        exif.ExiftoolProcess()

        assert len(captured) == 1
        assert captured[0][:3] == ["exiftool", "-stay_open", "True"]

    def test_init_uses_separate_stderr_pipe(self, monkeypatch):
        """Regression for photo-manager#145 Bug B (stream interleaving).

        ``stderr=subprocess.STDOUT`` merges the streams into a single OS
        pipe, allowing byte-level interleaving when the buffer fills.
        ``stderr=subprocess.PIPE`` keeps them on separate pipes.
        """
        from scanner import exif

        captured_kwargs: list[dict] = []

        def fake_popen(args, **kwargs):
            captured_kwargs.append(kwargs)
            return self._make_mock_proc([])

        monkeypatch.setattr(exif.subprocess, "Popen", fake_popen)
        exif.ExiftoolProcess()

        # stderr must be PIPE (separate), NOT STDOUT (merged).
        assert captured_kwargs[0]["stderr"] is exif.subprocess.PIPE
        assert captured_kwargs[0]["stderr"] is not exif.subprocess.STDOUT

    def test_init_passes_create_no_window_on_windows(self, monkeypatch):
        """Regression for #427: PyInstaller ``--noconsole`` build (PR #420)
        spawned a visible exiftool console window because Popen was called
        without ``creationflags``. The fix is to pass
        ``creationflags=_CREATE_NO_WINDOW`` (== Win32 ``CREATE_NO_WINDOW``
        == 0x08000000 on Windows; 0 on POSIX).

        Use the literal hex (0x08000000) rather than
        ``subprocess.CREATE_NO_WINDOW`` so this test runs on POSIX CI
        runners where the constant is undefined.
        """
        from scanner import exif

        captured_kwargs: list[dict] = []

        def fake_popen(args, **kwargs):
            captured_kwargs.append(kwargs)
            return self._make_mock_proc([])

        monkeypatch.setattr(exif.subprocess, "Popen", fake_popen)
        # Pin _CREATE_NO_WINDOW to the literal Win32 value so the assertion
        # is platform-independent (the module-level constant resolves to 0
        # on POSIX at import time).
        monkeypatch.setattr(exif, "_CREATE_NO_WINDOW", 0x08000000)
        exif.ExiftoolProcess()

        assert captured_kwargs[0]["creationflags"] == 0x08000000

    def test_init_creationflags_zero_on_non_windows(self, monkeypatch):
        """Pins the cross-platform contract: on POSIX the constant
        resolves to 0, which Popen accepts as a no-op ``creationflags``
        value. Asserts the kwarg is explicitly forwarded — not omitted —
        so the call shape stays uniform across platforms.
        """
        from scanner import exif

        captured_kwargs: list[dict] = []

        def fake_popen(args, **kwargs):
            captured_kwargs.append(kwargs)
            return self._make_mock_proc([])

        monkeypatch.setattr(exif.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(exif, "_CREATE_NO_WINDOW", 0)
        exif.ExiftoolProcess()

        assert "creationflags" in captured_kwargs[0]
        assert captured_kwargs[0]["creationflags"] == 0

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

    def test_execute_appends_stderr_after_stdout(self, monkeypatch):
        """If exiftool emitted on stderr, execute() must append that text
        after stdout (on a new line) so callers grepping for 'error' /
        'warning' still see them. JSON parsers slicing on ``[ ... ]``
        ignore the trailing text; this is the backward-compat shim."""
        import time
        from scanner import exif

        proc = self._make_mock_proc(
            stdout_lines=['[{"SourceFile": "/a.jpg"}]\n'],
            stderr_lines=["    1 image files read\n"],
        )
        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)

        et = exif.ExiftoolProcess()
        # Give the daemon thread a tick to drain the mock stderr line.
        time.sleep(0.05)
        out = et.execute(["-j", "-G", "-DateTimeOriginal", "/a.jpg"])

        assert "[" in out
        # The stderr line should be appended somewhere in the output.
        assert "image files read" in out

    def test_execute_stops_on_empty_readline(self, monkeypatch):
        """If readline returns '' (EOF), execute breaks out of the loop."""
        from scanner import exif

        proc = self._make_mock_proc([])
        # Override stdout to return a single line then EOF — no {ready}.
        proc.stdout.readline.side_effect = ["line1\n", ""]

        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)
        et = exif.ExiftoolProcess()
        out = et.execute(["-DateTimeOriginal"])
        assert "line1" in out

    def test_close_sends_stay_open_false(self, monkeypatch):
        """close() writes '-stay_open\\nFalse\\n' and waits for the process."""
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

    def test_execute_raises_timeout_when_stdout_idle(self, monkeypatch):
        """#465 — when exiftool's stdout produces no line within the
        ``read_timeout`` window, ``execute()`` must raise
        ``ExiftoolTimeout`` instead of blocking ``readline()`` forever.

        This is the regression that pre-#465 wedged the entire scan
        worker: a corrupt input / dropped NAS / kernel pipe stall would
        leave the consumer thread stuck inside ``readline()`` and the
        wider scan would deadlock until process exit. With the timeout
        the wedge surfaces as a clean exception the caller can act on
        (close + rotate to a fresh ExiftoolProcess).
        """
        import threading as _threading
        from scanner import exif

        # Block stdout.readline forever — simulates a wedged exiftool.
        # The drain thread will sit inside readline; the queue stays
        # empty; execute()'s queue.get(timeout=...) must give up.
        blocker = _threading.Event()  # never set → wait blocks forever

        def blocking_readline():
            blocker.wait()
            return ""

        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline.side_effect = blocking_readline
        proc.stderr = MagicMock()
        proc.stderr.readline.side_effect = lambda: ""   # immediate EOF

        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)
        et = exif.ExiftoolProcess()

        # Short timeout so the test completes quickly; production
        # default is 60s (constant in scanner/exif.py).
        with pytest.raises(exif.ExiftoolTimeout) as exc_info:
            et.execute(["-DateTimeOriginal"], read_timeout=0.1)

        # Error message names the timeout window so triage from a log
        # snippet alone doesn't require reading the source.
        assert "0.1" in str(exc_info.value)
        assert "wedged" in str(exc_info.value).lower()

        # Unblock the drain thread so it can exit (it's a daemon so it
        # would die with the test process anyway, but cleaning up
        # explicitly keeps subsequent tests' state clean).
        blocker.set()

    def test_execute_normal_path_unaffected_by_timeout_default(self, monkeypatch):
        """Regression guard: the queue+timeout refactor must not change
        the happy-path return value. Same input → same output as the
        pre-#465 implementation."""
        from scanner import exif

        proc = self._make_mock_proc(
            ["[{\"SourceFile\":\"/a.jpg\",\"EXIF:DateTimeOriginal\":\"2024:01:01 12:00:00\"}]\n"],
        )
        monkeypatch.setattr(exif.subprocess, "Popen", lambda *a, **k: proc)
        et = exif.ExiftoolProcess()
        out = et.execute(["-j", "-G", "-DateTimeOriginal", "/a.jpg"])

        assert "2024:01:01 12:00:00" in out
        assert "/a.jpg" in out


# ── _read_chunk: JSON structural guarantees (the photo-manager#145 fixes) ─


class TestReadChunkJSONStructural:
    """Tests that pin the structural guarantees of the JSON-based parser.

    The whole point of switching from line-positional to JSON parsing is
    that records bind to paths by ``SourceFile`` identity, not by index.
    These tests verify that property holds against every shape that
    line-positional parsing was vulnerable to.

    With the previous line-positional parser these tests fail by
    construction (i.e. drift would assign one file's date to a different
    file's row). With JSON, they pass by construction. Together they
    make the bug class structurally impossible.
    """

    def test_records_returned_in_different_order_still_match(self):
        """exiftool guarantees records-in-input-order, but a structural
        regression-protective test should not rely on that. Build records
        in REVERSED order and verify each path still gets ITS OWN date."""
        paths = [
            Path("/fake/a.jpg"),
            Path("/fake/b.jpg"),
            Path("/fake/c.jpg"),
        ]
        # Records returned in c, b, a order — the OPPOSITE of input order.
        et = _make_mock_et([
            (paths[2], {"EXIF:DateTimeOriginal": "2024:03:03 09:00:00"}),
            (paths[1], {"EXIF:DateTimeOriginal": "2024:02:02 09:00:00"}),
            (paths[0], {"EXIF:DateTimeOriginal": "2024:01:01 09:00:00"}),
        ])
        result = _read_chunk(paths, et)

        assert result[paths[0]] == datetime(2024, 1, 1, 9, 0, 0)
        assert result[paths[1]] == datetime(2024, 2, 2, 9, 0, 0)
        assert result[paths[2]] == datetime(2024, 3, 3, 9, 0, 0)

    def test_extra_records_for_unknown_paths_ignored(self):
        """If exiftool emits a record for a path we didn't ask for (a
        ghost-binding), it must NOT pollute results for any of the paths
        we did ask for."""
        paths = [Path("/fake/a.jpg"), Path("/fake/b.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:01:01 09:00:00"}),
            (paths[1], {"EXIF:DateTimeOriginal": "2024:02:02 09:00:00"}),
            ("/fake/ghost.jpg", {"EXIF:DateTimeOriginal": "1999:12:31 23:59:59"}),
        ])
        result = _read_chunk(paths, et)

        assert result[paths[0]] == datetime(2024, 1, 1, 9, 0, 0)
        assert result[paths[1]] == datetime(2024, 2, 2, 9, 0, 0)
        # Ghost record's date is nowhere in the result.
        assert datetime(1999, 12, 31, 23, 59, 59) not in result.values()

    def test_missing_record_yields_none_with_no_drift(self):
        """If exiftool fails to produce a record for path[1], path[2] must
        STILL get its own data — not path[1]'s. Position-independence."""
        paths = [
            Path("/fake/a.jpg"),
            Path("/fake/b.jpg"),
            Path("/fake/c.jpg"),
        ]
        # B is missing entirely from the JSON.
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:01:01 09:00:00"}),
            (paths[2], {"EXIF:DateTimeOriginal": "2024:03:03 09:00:00"}),
        ])
        result = _read_chunk(paths, et)

        assert result[paths[0]] == datetime(2024, 1, 1, 9, 0, 0)
        assert result[paths[1]] is None  # missing record
        assert result[paths[2]] == datetime(2024, 3, 3, 9, 0, 0)  # NOT shifted

    def test_status_message_in_output_does_not_break_parser(self):
        """When the wrapper has properly separated stdout/stderr, status
        messages appear AFTER the JSON. The slicer must ignore them."""
        paths = [Path("/fake/a.jpg")]
        et = _make_mock_et(
            [(paths[0], {"EXIF:DateTimeOriginal": "2024:01:01 09:00:00"})],
            extra_stderr="    1 image files read\n",
        )
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 9, 0, 0)

    def test_iphone_batch_all_three_tags_each_gets_own_date(self):
        """The signature failure mode of the pre-#145 parser: iPhone HEIC
        files where all three date tags are populated identically. The
        line-positional parser would short-circuit onto the previous
        file's tag value due to the ``or`` chain. JSON binds by path,
        so this is structurally correct."""
        paths = [Path(f"/fake/IMG_{i:04d}.HEIC") for i in range(4)]
        records = [
            (paths[0], {
                "EXIF:DateTimeOriginal": "2024:06:19 20:09:39",
                "EXIF:CreateDate": "2024:06:19 20:09:39",
                "QuickTime:CreateDate": "2024:06:19 20:09:39",
            }),
            (paths[1], {
                "EXIF:DateTimeOriginal": "2024:06:22 10:14:57",
                "EXIF:CreateDate": "2024:06:22 10:14:57",
                "QuickTime:CreateDate": "2024:06:22 10:14:57",
            }),
            (paths[2], {
                "EXIF:DateTimeOriginal": "2024:07:08 15:03:34",
                "EXIF:CreateDate": "2024:07:08 15:03:34",
                "QuickTime:CreateDate": "2024:07:08 15:03:34",
            }),
            (paths[3], {
                "EXIF:DateTimeOriginal": "2024:07:09 09:00:00",
                "EXIF:CreateDate": "2024:07:09 09:00:00",
                "QuickTime:CreateDate": "2024:07:09 09:00:00",
            }),
        ]
        et = _make_mock_et(records)
        result = _read_chunk(paths, et)

        assert result[paths[0]] == datetime(2024, 6, 19, 20, 9, 39)
        assert result[paths[1]] == datetime(2024, 6, 22, 10, 14, 57)
        assert result[paths[2]] == datetime(2024, 7, 8, 15, 3, 34)
        assert result[paths[3]] == datetime(2024, 7, 9, 9, 0, 0)

    def test_50_file_batch_no_drift(self):
        """Stress test — 50 distinct dates each must arrive at its own
        path. Catches any indexing regression at the tail."""
        paths = [Path(f"/fake/img_{i:03d}.jpg") for i in range(50)]
        records = [
            (paths[i], {
                "EXIF:DateTimeOriginal": f"2024:01:01 {i // 6:02d}:{(i % 6) * 10:02d}:00",
            })
            for i in range(50)
        ]
        et = _make_mock_et(records)
        result = _read_chunk(paths, et)

        for i, p in enumerate(paths):
            expected = datetime(2024, 1, 1, i // 6, (i % 6) * 10, 0)
            assert result[p] == expected, (
                f"file {i} mis-attributed: got {result[p]!r} expected {expected!r}"
            )


# ── _read_chunk: tag-fallback edge cases (with realistic JSON shape) ──


class TestReadChunkTagFallbacks:
    """Verify the EXIF/XMP/QuickTime fallback chain via realistic JSON
    records. Each record carries exactly the tags real exiftool would
    emit for that file type."""

    def test_exif_datetime_original_preferred_over_xmp(self):
        """When both EXIF and XMP DateTimeOriginal are present, EXIF wins
        per the key order in _JSON_DATE_KEYS."""
        paths = [Path("/fake/a.jpg")]
        et = _make_mock_et([
            (paths[0], {
                "EXIF:DateTimeOriginal": "2024:01:01 12:00:00",
                "XMP:DateTimeOriginal": "2024:06:01 12:00:00",
            }),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)

    def test_xmp_datetime_original_used_when_exif_absent(self):
        """PNG / GIF / WebP path: only XMP DateTimeOriginal is present."""
        paths = [Path("/fake/a.png")]
        et = _make_mock_et([
            (paths[0], {"XMP:DateTimeOriginal": "2024:03:15 14:00:00"}),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 3, 15, 14, 0, 0)

    def test_create_date_used_when_no_datetime_original(self):
        """Real ``createdate_only.jpg`` shape: only EXIF:CreateDate present."""
        paths = [Path("/fake/a.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:CreateDate": "2024:05:03 09:30:00"}),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 5, 3, 9, 30, 0)

    def test_quicktime_create_date_used_for_video(self):
        """Real ``dummy.mov`` shape: only QuickTime:CreateDate present."""
        paths = [Path("/fake/clip.mov")]
        et = _make_mock_et([
            (paths[0], {"QuickTime:CreateDate": "2023:12:25 18:00:00"}),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2023, 12, 25, 18, 0, 0)

    def test_dash_sentinel_in_datetime_original_falls_through(self):
        """Real ``dash_sentinel.jpg``: EXIF:DateTimeOriginal is literally
        ``"-"``. parse_exif_date returns None for that, the loop continues
        to the next key. With no other valid tag → result is None."""
        paths = [Path("/fake/a.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "-"}),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] is None

    def test_zero_date_falls_through_to_create_date(self):
        """A file whose DateTimeOriginal is the zero-date sentinel
        '0000:00:00 00:00:00' should fall through to CreateDate, not
        latch onto the sentinel."""
        paths = [Path("/fake/a.jpg")]
        et = _make_mock_et([
            (paths[0], {
                "EXIF:DateTimeOriginal": "0000:00:00 00:00:00",
                "EXIF:CreateDate": "2024:03:15 09:00:00",
            }),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 3, 15, 9, 0, 0)

    def test_subsecond_truncation_per_row(self):
        paths = [Path(f"/fake/sub{i}.jpg") for i in range(3)]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:05:02 09:30:00.500"}),
            (paths[1], {"EXIF:DateTimeOriginal": "2024:05:02 09:30:01.123"}),
            (paths[2], {"EXIF:DateTimeOriginal": "2024:05:02 09:30:02.999"}),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 5, 2, 9, 30, 0)
        assert result[paths[1]] == datetime(2024, 5, 2, 9, 30, 1)
        assert result[paths[2]] == datetime(2024, 5, 2, 9, 30, 2)

    def test_tz_suffix_per_row(self):
        paths = [Path(f"/fake/tz{i}.jpg") for i in range(3)]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:05:01 09:30:00+09:00"}),
            (paths[1], {"EXIF:DateTimeOriginal": "2024:05:01 09:30:00-05:00"}),
            (paths[2], {"EXIF:DateTimeOriginal": "2024:05:01 09:30:00+05:45"}),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 5, 1, 9, 30, 0)
        assert result[paths[1]] == datetime(2024, 5, 1, 9, 30, 0)
        assert result[paths[2]] == datetime(2024, 5, 1, 9, 30, 0)

    def test_path_with_spaces_or_unicode(self):
        """Real users have folder names like '東京旅行' or 'My Photos'.
        The path is the SourceFile string; pathlib normalises slashes,
        and JSON doesn't care about unicode in field values."""
        paths = [
            Path("/fake/My Photos/a.jpg"),
            Path("/fake/東京旅行/b.heic"),
            Path("/fake/café/c (copy).jpg"),
        ]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:01:01 12:00:00"}),
            (paths[1], {"EXIF:DateTimeOriginal": "2024:01:02 12:00:00"}),
            (paths[2], {"EXIF:DateTimeOriginal": "2024:01:03 12:00:00"}),
        ])
        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 2, 12, 0, 0)
        assert result[paths[2]] == datetime(2024, 1, 3, 12, 0, 0)


# ── _read_chunk: boundary cases ────────────────────────────────────────


class TestReadChunkBoundaries:
    """Pathological / degenerate inputs at the chunk boundary."""

    def test_empty_output_yields_none_for_all_paths(self):
        """If exiftool emits nothing (process died, args invalid) the
        JSON parser returns [], and every path gets None."""
        paths = [Path(f"/fake/x{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = ""

        result = _read_chunk(paths, et)
        assert all(result[p] is None for p in paths)
        assert set(result.keys()) == set(paths)

    def test_only_summary_yields_none_for_all_paths(self):
        """exiftool successfully ran but reported zero files. Output is
        just the summary line. No JSON brackets → parse returns []."""
        paths = [Path(f"/fake/x{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = "    0 image files read"

        result = _read_chunk(paths, et)
        assert all(result[p] is None for p in paths)

    def test_empty_array_yields_none_for_all_paths(self):
        """exiftool emitted ``[]\\n0 image files read`` (zero matching files)."""
        paths = [Path(f"/fake/x{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = "[]\n    0 image files read"

        result = _read_chunk(paths, et)
        assert all(result[p] is None for p in paths)

    def test_malformed_json_yields_none_for_all_paths(self):
        """If interleaved garbage corrupted the JSON (would-be Bug B
        symptom on the old wrapper), every path gets None — never a
        partially-corrupt result for some paths."""
        paths = [Path(f"/fake/x{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = '[{"SourceFile": "/fake/x0.jpg", "EXIF:DateTimeOriginal": broken'

        result = _read_chunk(paths, et)
        assert all(result[p] is None for p in paths)

    def test_chunk_size_one_works_through_batch_read_dates(self):
        """When chunk_size=1, every chunk has exactly one record."""
        import json as _json

        paths = [Path(f"/fake/c{i}.jpg") for i in range(3)]
        call_count = [0]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            assert len(chunk_paths) == 1
            idx = call_count[0]
            call_count[0] += 1
            records = [{
                "SourceFile": chunk_paths[0],
                "EXIF:DateTimeOriginal": f"2024:01:0{idx + 1} 12:00:00",
            }]
            return _json.dumps(records)

        et = MagicMock()
        et.execute.side_effect = fake_execute

        result = batch_read_dates(paths, et, chunk_size=1)
        assert et.execute.call_count == 3
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 2, 12, 0, 0)
        assert result[paths[2]] == datetime(2024, 1, 3, 12, 0, 0)

    def test_record_with_non_string_sourcefile_skipped(self):
        """Defensive: if a record's SourceFile is None or a number (which
        real exiftool never emits but a corrupt blob might), it shouldn't
        crash the binding step — just be ignored."""
        import json as _json

        paths = [Path("/fake/a.jpg")]
        records = [
            {"SourceFile": None, "EXIF:DateTimeOriginal": "1999:12:31 23:59:59"},
            {"SourceFile": "/fake/a.jpg", "EXIF:DateTimeOriginal": "2024:01:01 09:00:00"},
        ]
        et = MagicMock()
        et.execute.return_value = _json.dumps(records)

        result = _read_chunk(paths, et)
        # None-SourceFile record is ignored; the legit one wins.
        assert result[paths[0]] == datetime(2024, 1, 1, 9, 0, 0)


# ── Real-life exiftool output snapshots ────────────────────────────────


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "exiftool_outputs"


class TestRealExiftoolFixtures:
    """Tests fed by captured real-exiftool ``-j -G`` output from the
    qa/sandbox/ fixtures.

    Snapshot-style: a one-time live capture of exiftool against real
    on-disk files, saved as a plain-text file, then replayed through the
    parser in unit-test land. No live exiftool dependency at test-run
    time, but the input is shape-and-content-faithful to what users hit
    in practice.

    If exiftool ever changes its output format (different JSON key
    casing, different Group prefix scheme, etc.), the static fixtures go
    stale and these tests fail loudly. That's the point — we want to
    know.

    Recapture procedure (manual, when the fixtures need refreshing):

        from scanner.exif import ExiftoolProcess
        with ExiftoolProcess() as et:
            args = ['-j', '-G', '-DateTimeOriginal', '-CreateDate',
                    '-QuickTime:CreateDate', '-fast']
            args += [str(p) for p in paths]
            print(et.execute(args))

    Then write the printed output to the fixture file (one trailing
    newline; preserve exact whitespace including the leading 4-space
    indent on the summary line).
    """

    def _replay(self, fixture_name: str, paths: list[Path]) -> dict:
        """Replay a captured fixture through ``_read_chunk``."""
        output = (_FIXTURE_DIR / fixture_name).read_text(encoding="utf-8").rstrip("\n")
        et = MagicMock()
        et.execute.return_value = output
        return _read_chunk(paths, et)

    def test_exif_edge_batch_all_six_resolve_correctly(self):
        """Replay the 6-file capture from qa/sandbox/exif-edge/, exercising
        every edge-case file in one batch. Each fixture has a known
        intended date (or None for sentinels)."""
        paths = [
            Path("qa/sandbox/exif-edge/createdate_only.jpg"),
            Path("qa/sandbox/exif-edge/dash_sentinel.jpg"),
            Path("qa/sandbox/exif-edge/datetime_tag_only.jpg"),
            Path("qa/sandbox/exif-edge/subsecond.jpg"),
            Path("qa/sandbox/exif-edge/tz_offset.jpg"),
            Path("qa/sandbox/exif-edge/zero_date_sentinel.jpg"),
        ]
        result = self._replay("exif_edge_batch.txt", paths)

        # createdate_only: DateTimeOriginal absent, CreateDate populated.
        assert result[paths[0]] == datetime(2024, 5, 3, 9, 30, 0)
        # dash_sentinel: EXIF:DateTimeOriginal is literally "-", no fallback works → None.
        assert result[paths[1]] is None
        # datetime_tag_only: written via the bare DateTime tag, none of
        # our queried keys present → record exists but no match → None.
        assert result[paths[2]] is None
        # subsecond: '.500' truncated by the [:19] slice.
        assert result[paths[3]] == datetime(2024, 5, 2, 9, 30, 0)
        # tz_offset: '+09:00' suffix dropped.
        assert result[paths[4]] == datetime(2024, 5, 1, 9, 30, 0)
        # zero_date_sentinel: '0000:...' rejected by the prefix check.
        assert result[paths[5]] is None

    def test_mixed_batch_resolves_per_format(self):
        """Replay a 4-file mixed-format capture (HEIC + JPG + MOV + MP4)."""
        paths = [
            Path("qa/sandbox/formats/fmt_heic.heic"),
            Path("qa/sandbox/unique/unique_00.jpg"),
            Path("qa/sandbox/videos/dummy.mov"),
            Path("qa/sandbox/videos/dummy.mp4"),
        ]
        result = self._replay("mixed_batch.txt", paths)

        assert result[paths[0]] == datetime(2024, 4, 1, 10, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[2]] is None
        assert result[paths[3]] is None

    def test_fixture_files_exist(self):
        """Sanity check: ensure the fixture files are present in the
        package. Catches packaging regressions if `__init__.py` files
        get removed."""
        assert (_FIXTURE_DIR / "exif_edge_batch.txt").exists()
        assert (_FIXTURE_DIR / "mixed_batch.txt").exists()


# ── batch_read_extracts (#187 — PR 2) ──────────────────────────────────────


class TestBatchReadExtracts:
    """Extended exiftool batch for the scoring system.

    Critical sentinel contract: after this function runs, ``gps_present`` and
    ``xmp_derived`` are *never* None — they are always explicit booleans
    (False = checked and absent, True = present). A None on either field
    post-pipeline is the silent-dropout regression we are protecting against.
    """

    def test_empty_paths_returns_empty(self):
        from scanner.exif import batch_read_extracts
        et = MagicMock()
        result = batch_read_extracts([], et)
        assert result == {}
        et.execute.assert_not_called()

    def test_gps_present_true_when_latitude_in_record(self):
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {
                "EXIF:GPSLatitude": "37 deg 46' 0.00\" N",
                "EXIF:GPSLongitude": "122 deg 25' 0.00\" W",
            }),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].gps_present is True

    def test_gps_present_false_when_no_gps_tags(self):
        """Silent-dropout regression: an image without GPS must yield
        gps_present=False (not None), proving the exiftool pass ran."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:06:15 10:30:00"}),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].gps_present is False  # NOT None

    def test_gps_present_true_for_video_quicktime_coords(self):
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/clip.mov")]
        et = _make_mock_et([
            (paths[0], {"QuickTime:GPSCoordinates": "37.7,-122.4"}),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].gps_present is True

    def test_xmp_derived_true_when_derivedfrom_present(self):
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/edited.jpg")]
        et = _make_mock_et([
            (paths[0], {"XMP:DerivedFrom": "uuid:original-abc-123"}),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].xmp_derived is True

    def test_xmp_derived_false_when_absent(self):
        """Silent-dropout regression for xmp_derived."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([(paths[0], None)])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].xmp_derived is False  # NOT None

    def test_xmp_rating_parsed_as_int(self):
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {"XMP:Rating": 5}),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].xmp_rating == 5

    def test_xmp_rating_string_coerced(self):
        """exiftool sometimes emits Rating as string; defensive coerce."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {"XMP:Rating": "4"}),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].xmp_rating == 4

    def test_xmp_rating_none_when_absent(self):
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([(paths[0], None)])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].xmp_rating is None

    def test_exif_tag_count_counts_census_tags(self):
        """Count includes only tags in the documented census set
        (image + video tags). Non-census tags like ``EXIF:CreateDate``
        do not contribute even when present in the record."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {
                "EXIF:DateTimeOriginal": "2024:06:15 10:30:00",
                "EXIF:Make": "Canon",
                "EXIF:Model": "EOS 5D",
                "EXIF:ISO": "400",
                # EXIF:CreateDate is in the date fallback chain but NOT in
                # the census — counting it would double-credit dates.
                "EXIF:CreateDate": "2024:06:15 10:30:00",
            }),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].exif_tag_count == 4

    def test_exif_tag_count_zero_when_no_census_tags(self):
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([(paths[0], None)])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].exif_tag_count == 0

    def test_exif_date_tag_records_source_tag(self):
        """When DateTimeOriginal produces the date, exif_date_tag carries
        the exact tag name so the date_provenance scorer (PR 3) can
        weight DateTimeOriginal-derived dates higher than CreateDate
        fallbacks."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:DateTimeOriginal": "2024:06:15 10:30:00"}),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].exif_date == datetime(2024, 6, 15, 10, 30, 0)
        assert result[paths[0]].exif_date_tag == "EXIF:DateTimeOriginal"

    def test_exif_date_tag_fallback_path(self):
        """When DateTimeOriginal is absent, the tag name reflects which
        tag actually produced the date (CreateDate, QuickTime:CreateDate,
        etc.)."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([
            (paths[0], {"EXIF:CreateDate": "2024:01:01 08:00:00"}),
        ])
        result = batch_read_extracts(paths, et)
        assert result[paths[0]].exif_date == datetime(2024, 1, 1, 8, 0, 0)
        assert result[paths[0]].exif_date_tag == "EXIF:CreateDate"

    def test_extracted_by_marks_exiftool(self):
        """Every MediaExtract returned must have ``"exiftool"`` in
        extracted_by — that's how merge_extracts knows this partial came
        from exiftool and applies the exiftool-wins-on-exif_date rule."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([(paths[0], None)])
        result = batch_read_extracts(paths, et)
        assert "exiftool" in result[paths[0]].extracted_by

    def test_missing_record_returns_partial_with_error(self):
        """If exiftool returns no record for a path (e.g. file vanished
        mid-batch), we still emit a MediaExtract with extracted_by={
        'exiftool'} and an error message — never silently drop the path."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([])  # empty output — no records for any path
        result = batch_read_extracts(paths, et)
        assert paths[0] in result
        assert "exiftool" in result[paths[0]].extracted_by
        assert len(result[paths[0]].extraction_errors) == 1
        # All signals stay None for missing records (the scorer treats them
        # as "no signal").
        assert result[paths[0]].gps_present is None

    def test_chunking_calls_execute_multiple_times(self):
        """Same chunking semantics as batch_read_dates — chunk_size paths
        per execute call."""
        import json as _json
        from scanner.exif import batch_read_extracts

        paths = [Path(f"/fake/{i}.jpg") for i in range(5)]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            records = [{"SourceFile": p} for p in chunk_paths]
            return _json.dumps(records)

        et = MagicMock()
        et.execute.side_effect = fake_execute

        batch_read_extracts(paths, et, chunk_size=2)
        assert et.execute.call_count == 3  # ceil(5/2)

    def test_args_omit_fast_flag(self):
        """Critical: the extended batch must NOT use ``-fast``. GPS and
        XMP tags live in segments past the first IFD that ``-fast`` skips,
        so including it would silently zero out gps_present / xmp_derived
        for every file. Regression-protect that decision."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([(paths[0], None)])
        batch_read_extracts(paths, et)
        called_args = et.execute.call_args[0][0]
        assert "-fast" not in called_args

    def test_args_include_gps_and_xmp_selectors(self):
        """Sanity check that the new tag selectors actually reach exiftool.
        If someone removes ``-GPSLatitude`` later, gps_present would always
        be False — caught here at the args layer instead of in production."""
        from scanner.exif import batch_read_extracts
        paths = [Path("/fake/img.jpg")]
        et = _make_mock_et([(paths[0], None)])
        batch_read_extracts(paths, et)
        called_args = et.execute.call_args[0][0]
        # GPS
        assert "-GPSLatitude" in called_args
        assert "-QuickTime:GPSCoordinates" in called_args
        # XMP provenance + rating
        assert "-XMP-xmpMM:DerivedFrom" in called_args
        assert "-XMP:Rating" in called_args


# ── HashResult.to_media_extract adapter (#187 — PR 2) ──────────────────────


class TestHashResultToMediaExtract:
    """HashResult is the existing single-read hasher output. The adapter
    converts it into a partial MediaExtract that merge_extracts can combine
    with the exiftool partial.

    extracted_by must reflect which tools actually contributed data so the
    merge step's rawpy-wins-on-dims rule fires correctly.
    """

    def test_jpeg_extract_marks_hasher_and_pil(self):
        from scanner.dedup import HashResult
        from scanner.walker import FileRecord
        rec = FileRecord(
            path=Path("/x/a.jpg"), source_label="src",
            file_type="jpeg",
        )
        hr = HashResult(
            record=rec, sha256="abc", phash="defg",
            exif_date=datetime(2024, 1, 1, 12, 0, 0),
            mean_color="100,120,140",
            pixel_width=4032, pixel_height=3024,
        )
        me = hr.to_media_extract()
        assert me.path == Path("/x/a.jpg")
        assert me.file_type == "jpeg"
        assert me.sha256 == "abc"
        assert me.phash == "defg"
        assert me.mean_color == "100,120,140"
        assert me.pixel_width == 4032
        assert me.extracted_by == {"hasher", "pil"}

    def test_raw_extract_marks_rawpy(self):
        """For RAW files with dims, rawpy must be in extracted_by so
        merge_extracts picks rawpy's sensor dims over PIL's thumbnail."""
        from scanner.dedup import HashResult
        from scanner.walker import FileRecord
        rec = FileRecord(
            path=Path("/x/photo.nef"), source_label="src",
            file_type="raw",
        )
        hr = HashResult(
            record=rec, sha256="abc", phash="thumbphash",
            exif_date=None,  # RAW dates come from exiftool, not PIL
            pixel_width=6000, pixel_height=4000,
        )
        me = hr.to_media_extract()
        assert "rawpy" in me.extracted_by
        assert "pil" in me.extracted_by   # phash means PIL also ran
        assert "hasher" in me.extracted_by

    def test_video_extract_marks_only_hasher(self):
        """Video files: only sha256 is computed (streamed); no PIL, no rawpy."""
        from scanner.dedup import HashResult
        from scanner.walker import FileRecord
        rec = FileRecord(
            path=Path("/x/clip.mov"), source_label="src",
            file_type="mov",
        )
        hr = HashResult(
            record=rec, sha256="abc", phash=None,
            exif_date=None,
        )
        me = hr.to_media_extract()
        assert me.extracted_by == {"hasher"}

    def test_no_exif_date_tag_from_pil(self):
        """PIL doesn't surface which EXIF IFD/tag produced the date, so
        exif_date_tag is None on a PIL-only partial. The exiftool partial
        fills it in during merge."""
        from scanner.dedup import HashResult
        from scanner.walker import FileRecord
        rec = FileRecord(
            path=Path("/x/a.jpg"), source_label="src",
            file_type="jpeg",
        )
        hr = HashResult(
            record=rec, sha256="abc", phash="defg",
            exif_date=datetime(2024, 1, 1, 12, 0, 0),
        )
        me = hr.to_media_extract()
        assert me.exif_date == datetime(2024, 1, 1, 12, 0, 0)
        assert me.exif_date_tag is None
