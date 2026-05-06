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

def _make_mock_et(
    lines_per_file: list[tuple[str, str, str]],
    paths: list[Path] | None = None,
) -> MagicMock:
    """Build a mock ExiftoolProcess that returns realistic output lines.

    Mirrors what real ``exiftool -stay_open True ... -s3 -f`` emits, verified
    live against ``qa/sandbox/`` fixtures:

    * **Single file** (``len(lines_per_file) == 1``): 3 lines, one per tag,
      no header — exiftool only emits the ``======== <path>`` separator
      when processing >1 file in a stay_open batch.
    * **Multi-file** (>1): per-file ``======== <path>`` header line + 3 tag
      lines + a trailing ``    N image files read`` summary line. Total is
      ``4N + 1`` lines for N files.

    If ``paths`` is None, synthetic paths ``/fake/file{i}.jpg`` are stitched
    into the headers — sufficient for tests that only assert on result
    values, not on header content. Tests that need realistic paths in the
    headers should pass ``paths`` explicitly.

    History: this helper used to emit a flat ``3 * N`` lines with no headers,
    matching the pre-#145 buggy parser's mental model. The parser indexed
    against that shape and the mock provided exactly that shape, so tests
    passed by tautology rather than because the parser was correct against
    real exiftool. The realistic shape forces tests to exercise the
    metaline-stripping branch in ``_read_chunk``.
    """
    n = len(lines_per_file)
    if paths is None:
        paths = [Path(f"/fake/file{i}.jpg") for i in range(n)]
    if len(paths) != n:
        raise ValueError(
            f"paths length {len(paths)} does not match lines_per_file length {n}"
        )

    responses: list[str] = []
    if n <= 1:
        # Single-file mode — no header, no summary. Real exiftool behaviour.
        for dto, create, qt_create in lines_per_file:
            responses.extend([dto, create, qt_create])
    else:
        # Multi-file mode — per-file header + 3 tag lines + trailing summary.
        for path, (dto, create, qt_create) in zip(paths, lines_per_file):
            responses.append(f"======== {path}")
            responses.extend([dto, create, qt_create])
        responses.append(f"    {n} image files read")

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

        def fake_execute(args):
            # Mirror real exiftool stay_open shape: per-file header + 3 dash
            # tag lines + trailing summary. Single-file chunk has no header.
            chunk_paths = [a for a in args if not a.startswith("-")]
            n = len(chunk_paths)
            if n <= 1:
                return "\n".join(["-", "-", "-"])
            lines = []
            for p in chunk_paths:
                lines.append(f"======== {p}")
                lines.extend(["-", "-", "-"])
            lines.append(f"    {n} image files read")
            return "\n".join(lines)

        et = MagicMock()
        et.execute.side_effect = fake_execute

        batch_read_dates(paths, et, chunk_size=2)
        assert et.execute.call_count == 3  # ceil(5/2)

    def test_chunking_returns_all_paths(self):
        """All input paths should appear as keys in the result."""
        paths = [Path(f"/fake/{i}.jpg") for i in range(7)]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            n = len(chunk_paths)
            if n <= 1:
                return "\n".join(["-", "-", "-"])
            lines = []
            for p in chunk_paths:
                lines.append(f"======== {p}")
                lines.extend(["-", "-", "-"])
            lines.append(f"    {n} image files read")
            return "\n".join(lines)

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


# ── #145 regression: exiftool stay_open header lines ────────────────────


class TestReadChunkStayOpenHeaders:
    """Regression for #145: in real ``-stay_open`` multi-file output, exiftool
    emits a ``======== <path>`` header before each file's tag block AND a
    trailing ``    N image files read`` summary. If those metalines aren't
    filtered, ``i * 3`` indexing drifts and each file past index 0 gets dates
    drawn from a different file in the same batch.

    The mock used elsewhere in this file (``_make_mock_et``) only emits the
    ``len(paths) * 3`` clean tag values — that shape never triggers the bug
    because the parser's mental model and the mock agree by accident. These
    tests feed the realistic shape and assert each path keeps its own date.
    """

    @staticmethod
    def _real_stay_open_output(paths_and_dates: list[tuple[str, str, str, str]]) -> str:
        """Build an exiftool stay_open multi-file response.

        Each tuple is ``(path, dt_orig, create, qt_create)``. Output mirrors
        what real exiftool emits for ``-DateTimeOriginal -CreateDate
        -QuickTime:CreateDate -s3 -f`` against >1 file: per-file header
        ``======== <path>`` plus 3 tag values, ending in
        ``    N image files read``.
        """
        chunks: list[str] = []
        for path, dto, create, qt_create in paths_and_dates:
            chunks.append(f"======== {path}")
            chunks.append(dto)
            chunks.append(create)
            chunks.append(qt_create)
        chunks.append(f"    {len(paths_and_dates)} image files read")
        return "\n".join(chunks)

    def test_three_files_each_gets_own_date(self):
        """Three iPhone-style files (all 3 date tags valid). Without the
        metaline filter, file index 1+ inherits the previous file's QT
        date via the dt_orig short-circuit; this test pins correct
        attribution."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/img{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = self._real_stay_open_output([
            (str(paths[0]), "2024:06:19 20:09:39", "2024:06:19 20:09:39", "2024:06:19 20:09:39"),
            (str(paths[1]), "2024:06:22 10:14:57", "2024:06:22 10:14:57", "2024:06:22 10:14:57"),
            (str(paths[2]), "2024:07:08 15:03:34", "2024:07:08 15:03:34", "2024:07:08 15:03:34"),
        ])

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 6, 19, 20, 9, 39)
        assert result[paths[1]] == datetime(2024, 6, 22, 10, 14, 57)
        assert result[paths[2]] == datetime(2024, 7, 8, 15, 3, 34)

    def test_mixed_tags_each_file_resolves_correctly(self):
        """Like the qa/sandbox/unique fixture: only DateTimeOriginal populated,
        other tags are ``-``. Without the filter, file index ≥2 ends up with
        ``None`` because the lines at base/base+1/base+2 are ``-``, ``-``,
        and a header line — none parse to a date."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/u{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = self._real_stay_open_output([
            (str(paths[0]), "2024:01:01 12:00:00", "-", "-"),
            (str(paths[1]), "2024:01:02 12:00:00", "-", "-"),
            (str(paths[2]), "2024:01:03 12:00:00", "-", "-"),
        ])

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 2, 12, 0, 0)
        assert result[paths[2]] == datetime(2024, 1, 3, 12, 0, 0)

    def test_two_files_minimum_to_trigger_headers(self):
        """Single-file mode emits no header (verified live), so the bug
        only surfaces from 2 files onward. Pin the smallest case that
        has the header at all."""
        from scanner.exif import _read_chunk

        paths = [Path("/fake/a.jpg"), Path("/fake/b.heic")]
        et = MagicMock()
        et.execute.return_value = self._real_stay_open_output([
            (str(paths[0]), "2024:09:24 21:33:20", "-", "-"),
            (str(paths[1]), "2024:06:12 11:17:37", "-", "-"),
        ])

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 9, 24, 21, 33, 20)
        assert result[paths[1]] == datetime(2024, 6, 12, 11, 17, 37)

    def test_image_files_updated_summary_also_filtered(self):
        """Some exiftool flag combinations end with 'image files updated'
        instead of 'image files read'. Both forms must be filtered so the
        helper survives if the args set ever changes (e.g. write mode)."""
        from scanner.exif import _strip_exiftool_metalines

        output = "\n".join([
            "======== /a.jpg",
            "2024:01:01 12:00:00", "-", "-",
            "    1 image files updated",
        ])
        assert _strip_exiftool_metalines(output) == ["2024:01:01 12:00:00", "-", "-"]


class TestStripExiftoolMetalines:
    """Direct coverage of the metaline filter."""

    def test_drops_per_file_headers(self):
        from scanner.exif import _strip_exiftool_metalines

        output = "\n".join([
            "======== /tmp/a.jpg",
            "2024:01:01 12:00:00",
            "-",
            "-",
        ])
        assert _strip_exiftool_metalines(output) == ["2024:01:01 12:00:00", "-", "-"]

    def test_drops_trailing_summary(self):
        from scanner.exif import _strip_exiftool_metalines

        output = "\n".join([
            "2024:01:01 12:00:00", "-", "-",
            "    3 image files read",
        ])
        assert _strip_exiftool_metalines(output) == ["2024:01:01 12:00:00", "-", "-"]

    def test_passes_through_clean_output_unchanged(self):
        """Single-file output has no metalines; the filter must be a no-op."""
        from scanner.exif import _strip_exiftool_metalines

        output = "\n".join(["2024:01:01 12:00:00", "-", "-"])
        assert _strip_exiftool_metalines(output) == ["2024:01:01 12:00:00", "-", "-"]

    def test_does_not_strip_dates_that_contain_words(self):
        """A pathological tag value containing 'image files read' as a
        substring should NOT be stripped — only a stripped-to-end match
        does. Real exiftool emits dates only, so this is defensive."""
        from scanner.exif import _strip_exiftool_metalines

        output = "fake image files read in middle\n2024:01:01 12:00:00"
        # First line ends with "in middle" — kept.
        assert _strip_exiftool_metalines(output) == [
            "fake image files read in middle",
            "2024:01:01 12:00:00",
        ]


# ── Date-parsing edge cases (value level) ──────────────────────────────


class TestParseExifDateEdgeCases:
    """Edge cases for ``parse_exif_date`` beyond the happy path.

    Dates are critical to this app — manifest entries, dedup grouping,
    sort-by-shot-date all depend on these parses being correct or
    explicitly None. Each test pins a specific input shape that real
    cameras / phones / scanners are known to produce (or that exiftool
    is known to emit in failure modes)."""

    def test_subsecond_resolution_is_truncated(self):
        """exiftool emits sub-second precision for cameras that record it
        (verified live on qa/sandbox/exif-edge/subsecond.jpg → '2024:05:02
        09:30:00.500'). The parser slices to 19 chars before strptime, so
        the fractional part is silently dropped."""
        from scanner.exif import parse_exif_date

        result = parse_exif_date("2024:05:02 09:30:00.500")
        assert result == datetime(2024, 5, 2, 9, 30, 0)

    def test_negative_timezone_offset_stripped(self):
        """Symmetric to the existing positive-tz test. Western-hemisphere
        cameras emit negative offsets (e.g. '-05:00' for EST, '-08:00' for
        PST). Slice to 19 chars drops the offset; we keep the wall-clock
        time."""
        from scanner.exif import parse_exif_date

        result = parse_exif_date("2024:06:01 12:00:00-05:00")
        assert result == datetime(2024, 6, 1, 12, 0, 0)

    def test_zero_offset_suffix_stripped(self):
        """UTC photos may emit '+00:00'. Same slice-to-19 rule applies."""
        from scanner.exif import parse_exif_date

        result = parse_exif_date("2024:06:01 12:00:00+00:00")
        assert result == datetime(2024, 6, 1, 12, 0, 0)

    def test_zero_date_with_surrounding_whitespace(self):
        """The zero-date check happens after .strip(), so a padded zero
        sentinel is still recognized."""
        from scanner.exif import parse_exif_date

        assert parse_exif_date("  0000:00:00 00:00:00  ") is None

    def test_date_only_no_time_returns_none(self):
        """Some malformed sources omit the time portion entirely. The
        parser's strptime expects %H:%M:%S and rejects a bare date."""
        from scanner.exif import parse_exif_date

        assert parse_exif_date("2024:06:01") is None

    def test_time_only_no_date_returns_none(self):
        """Inverse of the above. strptime rejects a bare time."""
        from scanner.exif import parse_exif_date

        assert parse_exif_date("12:00:00") is None

    def test_two_digit_year_returns_none(self):
        """Legacy formats sometimes emit '94:06:01 12:00:00'. strptime
        with %Y requires 4 digits and will reject this."""
        from scanner.exif import parse_exif_date

        assert parse_exif_date("94:06:01 12:00:00") is None

    def test_far_future_date_parses_fine(self):
        """No upper bound enforced — '2099:12:31 23:59:59' is a valid
        datetime. Pin this to catch any future bound that might be added
        defensively."""
        from scanner.exif import parse_exif_date

        result = parse_exif_date("2099:12:31 23:59:59")
        assert result == datetime(2099, 12, 31, 23, 59, 59)

    def test_leap_second_returns_none(self):
        """Python datetime rejects ':60' seconds (no leap-second support).
        Real cameras don't emit these but exiftool's %S could pass them
        through if a file was hand-edited."""
        from scanner.exif import parse_exif_date

        assert parse_exif_date("2024:06:30 23:59:60") is None

    def test_invalid_month_returns_none(self):
        """Month 13 — defensive guard against corrupt EXIF."""
        from scanner.exif import parse_exif_date

        assert parse_exif_date("2024:13:01 12:00:00") is None

    def test_invalid_day_returns_none(self):
        """Feb 30 — defensive guard against corrupt EXIF."""
        from scanner.exif import parse_exif_date

        assert parse_exif_date("2024:02:30 12:00:00") is None

    def test_zero_date_with_timezone_suffix_returns_none(self):
        """Edge: zero-date sentinel decorated with a tz offset. The
        sentinel check happens before slicing — actually checks the
        stripped raw — so '0000:00:00 00:00:00+09:00' must still
        return None."""
        from scanner.exif import parse_exif_date

        # Slice happens AFTER the zero check, so the prefix '0000:' is
        # what triggers the early-return.
        assert parse_exif_date("0000:00:00 00:00:00+09:00") is None


# ── _read_chunk batch-level edge cases (with realistic shape) ──────────


class TestReadChunkBatchEdges:
    """Multi-file batches stressing the metaline filter + fallback chain.

    All mocks here use ``_make_mock_et`` (which now emits the realistic
    ``======== <path>`` headers + trailing summary). Each test covers
    a real-world input shape exiftool is known to produce."""

    def test_iphone_batch_all_three_tags_valid(self):
        """iPhone HEIC files typically have all three date tags populated
        identically. Verify each row gets ITS OWN date even though every
        field on every row carries a valid date — the bug from #145 was
        most visible exactly in this case (the dt_orig short-circuit
        latches onto the previous file's QT date)."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/IMG_{i:04d}.HEIC") for i in range(4)]
        dates = [
            ("2024:06:19 20:09:39", "2024:06:19 20:09:39", "2024:06:19 20:09:39"),
            ("2024:06:22 10:14:57", "2024:06:22 10:14:57", "2024:06:22 10:14:57"),
            ("2024:07:08 15:03:34", "2024:07:08 15:03:34", "2024:07:08 15:03:34"),
            ("2024:07:09 09:00:00", "2024:07:09 09:00:00", "2024:07:09 09:00:00"),
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 6, 19, 20, 9, 39)
        assert result[paths[1]] == datetime(2024, 6, 22, 10, 14, 57)
        assert result[paths[2]] == datetime(2024, 7, 8, 15, 3, 34)
        assert result[paths[3]] == datetime(2024, 7, 9, 9, 0, 0)

    def test_mixed_tag_population(self):
        """Real folders mix tag patterns: HEIC has DateTimeOriginal,
        videos have only QuickTime:CreateDate, scans have only CreateDate.
        Each row's fallback chain must resolve independently — no
        cross-pollination."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/f{i}") for i in range(4)]
        dates = [
            ("2024:01:01 12:00:00", "-", "-"),                         # HEIC: dt_orig only
            ("-", "2024:02:02 12:00:00", "-"),                         # scanned JPG: create only
            ("-", "-", "2024:03:03 12:00:00"),                         # MOV: qt_create only
            ("-", "-", "-"),                                            # corrupt: nothing
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] == datetime(2024, 2, 2, 12, 0, 0)
        assert result[paths[2]] == datetime(2024, 3, 3, 12, 0, 0)
        assert result[paths[3]] is None

    def test_all_video_batch_falls_back_to_qt_create(self):
        """A folder of MOV/MP4 files: only QuickTime:CreateDate is
        populated. Verify all rows resolve via the third tag in the
        fallback chain, none accidentally pull from a neighbor."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/clip{i}.mov") for i in range(3)]
        dates = [
            ("-", "-", "2024:01:01 10:00:00"),
            ("-", "-", "2024:01:02 11:00:00"),
            ("-", "-", "2024:01:03 12:00:00"),
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 10, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 2, 11, 0, 0)
        assert result[paths[2]] == datetime(2024, 1, 3, 12, 0, 0)

    def test_zero_date_sentinel_falls_through_chain(self):
        """A file whose DateTimeOriginal is the zero-date sentinel
        '0000:00:00 00:00:00' should fall through to CreateDate, not
        latch onto the sentinel."""
        from scanner.exif import _read_chunk

        paths = [Path("/fake/a.jpg")]
        dates = [("0000:00:00 00:00:00", "2024:03:15 09:00:00", "-")]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 3, 15, 9, 0, 0)

    def test_path_with_spaces_in_header(self):
        """exiftool emits the path verbatim in the header, including
        spaces. The metaline filter must still recognize it as a header
        line (only requires the '======== ' prefix)."""
        from scanner.exif import _read_chunk

        paths = [
            Path("/fake/My Photos/a.jpg"),
            Path("/fake/My Photos/b (copy).jpg"),
        ]
        dates = [
            ("2024:01:01 12:00:00", "-", "-"),
            ("2024:01:02 12:00:00", "-", "-"),
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 2, 12, 0, 0)

    def test_path_with_unicode_in_header(self):
        """Real users have folder names like '東京旅行' or 'café'. The
        filter only checks for the '======== ' prefix, so unicode in
        the path content is irrelevant — but pin it as a regression
        guard."""
        from scanner.exif import _read_chunk

        paths = [
            Path("/fake/東京旅行/a.heic"),
            Path("/fake/café/b.jpg"),
        ]
        dates = [
            ("2024:01:01 12:00:00", "-", "-"),
            ("2024:01:02 12:00:00", "-", "-"),
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 2, 12, 0, 0)

    def test_50_file_batch_no_drift(self):
        """Stress test: 50 distinct dates, each must arrive at its own
        path. If any drift remains in the metaline filter (e.g. the
        trailing summary getting eaten by an off-by-one) it would
        manifest at the tail. Uses minutes-within-hour to keep dates
        unambiguously distinct without overflowing the time fields."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/img_{i:03d}.jpg") for i in range(50)]
        # Span 50 distinct (hour, minute) combos within one day:
        # 50 = 24h × 2 + 2 (use minute 0 and 30 for first 24h, then minute 0
        # again for hours 0/1). Simpler: use hour=i//6, minute=(i%6)*10.
        dates = [
            (f"2024:01:01 {i // 6:02d}:{(i % 6) * 10:02d}:00", "-", "-")
            for i in range(50)
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        for i, p in enumerate(paths):
            expected = datetime(2024, 1, 1, i // 6, (i % 6) * 10, 0)
            assert result[p] == expected, (
                f"file {i} mis-attributed: got {result[p]!r} expected {expected!r}"
            )

    def test_subsecond_in_batch_truncates_per_row(self):
        """Subsecond resolution lives on individual EXIF tags; verify
        each row's truncation happens independently in batch context."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/sub{i}.jpg") for i in range(3)]
        dates = [
            ("2024:05:02 09:30:00.500", "-", "-"),
            ("2024:05:02 09:30:01.123", "-", "-"),
            ("2024:05:02 09:30:02.999", "-", "-"),
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 5, 2, 9, 30, 0)
        assert result[paths[1]] == datetime(2024, 5, 2, 9, 30, 1)
        assert result[paths[2]] == datetime(2024, 5, 2, 9, 30, 2)

    def test_tz_suffix_in_batch_strips_per_row(self):
        """Timezone-suffixed dates from cameras with offset support.
        Same slice rule, applied per row, no drift."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/tz{i}.jpg") for i in range(3)]
        dates = [
            ("2024:05:01 09:30:00+09:00", "-", "-"),  # JST
            ("2024:05:01 09:30:00-05:00", "-", "-"),  # EST
            ("2024:05:01 09:30:00+05:45", "-", "-"),  # NPT (Nepal — quarter-hour)
        ]
        et = _make_mock_et(dates, paths=paths)

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 5, 1, 9, 30, 0)
        assert result[paths[1]] == datetime(2024, 5, 1, 9, 30, 0)
        assert result[paths[2]] == datetime(2024, 5, 1, 9, 30, 0)


# ── _read_chunk boundary cases ─────────────────────────────────────────


class TestReadChunkBoundaries:
    """Pathological / degenerate inputs at the chunk boundary."""

    def test_empty_output_yields_none_for_all_paths(self):
        """If exiftool emits nothing (process died, args invalid) the
        short-output guard must cover every path with None."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/x{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = ""

        result = _read_chunk(paths, et)
        assert all(result[p] is None for p in paths)
        assert set(result.keys()) == set(paths)

    def test_only_summary_yields_none_for_all_paths(self):
        """exiftool successfully ran but reported zero files (e.g. all
        paths were unreadable). Output is just the summary line. After
        stripping metalines, no data lines remain → all paths get None."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/x{i}.jpg") for i in range(3)]
        et = MagicMock()
        et.execute.return_value = "    0 image files read"

        result = _read_chunk(paths, et)
        assert all(result[p] is None for p in paths)

    def test_only_headers_no_data_yields_none(self):
        """Defensive: hypothetical malformed output with headers but no
        tag values. Metaline filter strips them all → no data lines →
        all paths get None."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/x{i}.jpg") for i in range(2)]
        et = MagicMock()
        et.execute.return_value = "\n".join([
            f"======== {paths[0]}",
            f"======== {paths[1]}",
            "    2 image files read",
        ])

        result = _read_chunk(paths, et)
        assert all(result[p] is None for p in paths)

    def test_chunk_size_one_works_through_batch_read_dates(self):
        """When chunk_size=1, every chunk is single-file (no header in
        real exiftool). Verify `_make_mock_et` and the parser cooperate
        in that degenerate case."""
        from scanner.exif import batch_read_dates

        paths = [Path(f"/fake/c{i}.jpg") for i in range(3)]

        call_count = [0]

        def fake_execute(args):
            chunk_paths = [a for a in args if not a.startswith("-")]
            n = len(chunk_paths)
            call_count[0] += 1
            # Single-file chunk → no header.
            if n <= 1:
                idx = call_count[0] - 1
                return f"2024:01:0{idx + 1} 12:00:00\n-\n-"
            raise AssertionError("expected single-file chunks only")

        et = MagicMock()
        et.execute.side_effect = fake_execute

        result = batch_read_dates(paths, et, chunk_size=1)
        assert et.execute.call_count == 3
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] == datetime(2024, 1, 2, 12, 0, 0)
        assert result[paths[2]] == datetime(2024, 1, 3, 12, 0, 0)

    def test_short_output_in_real_shape_partial_resolution(self):
        """Like ``test_short_output_yields_none_for_remaining_paths`` but
        with the realistic shape: 3 files declared, only 1 resolves
        before exiftool died mid-batch (no trailing summary)."""
        from scanner.exif import _read_chunk

        paths = [Path(f"/fake/x{i}.jpg") for i in range(3)]
        et = MagicMock()
        # Header + 3 tag lines for file 0, then exiftool died (no header
        # for file 1, no summary).
        et.execute.return_value = "\n".join([
            f"======== {paths[0]}",
            "2024:01:01 12:00:00", "-", "-",
        ])

        result = _read_chunk(paths, et)
        assert result[paths[0]] == datetime(2024, 1, 1, 12, 0, 0)
        assert result[paths[1]] is None
        assert result[paths[2]] is None


# ── Real-life exiftool output snapshots (the "real data" the user asked
# for) ─────────────────────────────────────────────────────────────────


_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "exiftool_outputs"


class TestRealExiftoolFixtures:
    """Tests fed by captured real-exiftool output from the qa/sandbox/
    fixtures.

    These are snapshot-style: a one-time live capture of exiftool against
    real on-disk files, saved as a plain-text file, then replayed through
    the parser in unit-test land. No live exiftool dependency at test-run
    time, but the input is shape-and-content-faithful to what users hit
    in practice.

    If exiftool ever changes its output format (`-stay_open` separator
    changes, summary line wording shifts, etc.), the static fixtures go
    stale and these tests fail loudly. That's the point — we want to
    know.

    Recapture procedure (manual, when the fixtures need refreshing):

        from scanner.exif import ExiftoolProcess
        with ExiftoolProcess() as et:
            args = ['-DateTimeOriginal', '-CreateDate',
                    '-QuickTime:CreateDate', '-s3', '-f', '-fast']
            args += [str(p) for p in paths]
            print(et.execute(args))

    Then write the printed output to the fixture file (one trailing
    newline; preserve leading whitespace exactly).
    """

    def _replay(self, fixture_name: str, paths: list[Path]) -> dict:
        """Replay a captured fixture through ``_read_chunk``."""
        from scanner.exif import _read_chunk

        output = (_FIXTURE_DIR / fixture_name).read_text(encoding="utf-8").rstrip("\n")
        et = MagicMock()
        et.execute.return_value = output
        return _read_chunk(paths, et)

    def test_exif_edge_batch_all_six_resolve_correctly(self):
        """Replay the 6-file capture from qa/sandbox/exif-edge/, exercising
        every edge-case file in one batch. Each fixture has a known
        intended date (or None for sentinels)."""
        # Order matches the captured fixture file. Paths must match the
        # `======== <path>` headers exactly so the parser sees the
        # captured output as-if our batch produced it.
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
        # dash_sentinel: every tag is "-", no fallback works → None.
        assert result[paths[1]] is None
        # datetime_tag_only: written via the bare DateTime tag (not
        # DateTimeOriginal). exiftool's -DateTimeOriginal arg doesn't
        # match it, so all three queried tags are "-" → None. Documents
        # a real gotcha: writing only the DateTime tag is invisible to
        # this scanner.
        assert result[paths[2]] is None
        # subsecond: '.500' truncated by the [:19] slice.
        assert result[paths[3]] == datetime(2024, 5, 2, 9, 30, 0)
        # tz_offset: '+09:00' suffix dropped.
        assert result[paths[4]] == datetime(2024, 5, 1, 9, 30, 0)
        # zero_date_sentinel: '0000:...' rejected by the prefix check.
        assert result[paths[5]] is None

    def test_mixed_batch_resolves_per_format(self):
        """Replay a 4-file mixed-format capture (HEIC + JPG + MOV + MP4).
        The MOV/MP4 are dummy files with no metadata, demonstrating the
        all-dashes → None path. The HEIC + JPG resolve via
        DateTimeOriginal."""
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
