"""Wedged-exiftool rotation in the scan pipeline's EXIF consumer.

Seam: ``run_pipeline`` imports ``ExiftoolProcess`` and ``batch_read_extracts``
from ``scanner.exif`` at call time, so monkeypatching those module attributes
reaches the real consumer thread. PNG inputs skip the in-memory JPEG path, so
every record goes through ``exif_queue`` and the exiftool consumer.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from core.app_service.cancel_token import _CancelToken
from core.app_service.dtos import ScanConfig
from core.app_service.scan_runner import run_pipeline
from scanner.exif import ExiftoolTimeout
from scanner.media_extract import MediaExtract

_FAKE_EXIF_DATE = datetime(2021, 5, 6, 7, 8, 9)


class _SpyBus:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.failed_msgs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def stage(self, *_a, **_kw) -> None:
        pass

    def failed(self, msg: str) -> None:
        self.failed_msgs.append(msg)

    def finished(self, _output_path: str) -> None:
        pass

    def completed_empty(self) -> None:
        pass

    def pool_measured(self, *_a, **_kw) -> None:
        pass

    def knee_measured(self, *_a, **_kw) -> None:
        pass


def _write_png(path: Path, shade: int) -> None:
    Image.new("RGB", (8, 8), (shade % 256, 0, 0)).save(path, "PNG")


def _install_fake_exiftool(monkeypatch, on_batch, on_construct=None):
    """Replace the exiftool seam with a recorder.

    ``on_batch(call_no, proc, paths)`` runs before every batch read and may
    raise. ``on_construct(attempt_no)`` runs before every ``ExiftoolProcess()``
    and may raise. Returns ``(created_procs, calls)``; ``calls`` holds
    ``(call_no, proc_index, paths)`` for every batch attempt.
    """
    import scanner.exif as _exif

    created: list = []
    calls: list = []

    class _FakeProcess:
        def __init__(self) -> None:
            if on_construct is not None:
                on_construct(len(created) + 1)
            self.index = len(created)
            self.closed = False
            self.killed = False
            created.append(self)

        def close(self) -> None:
            self.closed = True

        def kill(self) -> None:
            self.killed = True
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            self.close()

    def _fake_batch_read(paths, et, chunk_size=500):
        calls.append((len(calls) + 1, et.index, list(paths)))
        on_batch(len(calls), et, list(paths))
        return {
            p: MediaExtract(
                path=p, extracted_by={"exiftool"}, exif_date=_FAKE_EXIF_DATE
            )
            for p in paths
        }

    monkeypatch.setattr(_exif, "ExiftoolProcess", _FakeProcess)
    monkeypatch.setattr(_exif, "batch_read_extracts", _fake_batch_read)
    return created, calls


def _run_scan(
    tmp_path: Path, count: int, timeout: float = 60.0, token=None, bus=None
):
    """Run the real pipeline over ``count`` PNGs in a thread.

    Returns ``(rows, bus, finished)``; ``finished`` is False when the scan
    did not return within ``timeout`` (a deadlock).
    """
    from core.app_service.cancel_token import _CancelToken

    token = token or _CancelToken()
    bus = bus or _SpyBus()
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for i in range(count):
        _write_png(src / f"img{i:05d}.png", i)
    out = tmp_path / "manifest.sqlite"
    config = ScanConfig(
        sources={"src": src},
        output_path=out,
        recursive_map={"src": False},
        workers=1,
        exif_workers=1,
        hash_pool="thread",
    )
    errors: list[BaseException] = []

    def _target() -> None:
        try:
            run_pipeline(config, token, bus)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout)
    finished = not worker.is_alive()
    if errors:
        raise errors[0]
    # A cancelled run writes no manifest; only read rows from a completed run.
    rows = _manifest_rows(out) if finished and not token() else []
    return rows, bus, finished


def _manifest_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT source_path, shot_date FROM migration_manifest"
            ).fetchall()
        ]
    finally:
        conn.close()


def _has_fake_date(row: dict) -> bool:
    return bool(row["shot_date"]) and row["shot_date"].startswith("2021-05-06")


class TestWedgedExiftoolRotation:
    def test_timeout_on_batch_rotates_and_retries_same_batch(
        self, tmp_path, monkeypatch
    ):
        """A stalled first batch is retried on a fresh exiftool and every
        record gets its EXIF data; the wedged process is closed."""

        def _on_batch(call_no, _proc, _paths):
            if call_no == 1:
                raise ExiftoolTimeout("read stalled")

        created, calls = _install_fake_exiftool(monkeypatch, _on_batch)

        rows, bus, finished = _run_scan(tmp_path, 3)

        assert finished, "scan must finish after a wedged exiftool"
        assert bus.failed_msgs == []
        assert len(created) == 2, f"expected original + one rotation; got {len(created)}"
        assert created[0].closed, "the wedged process must be closed on rotation"
        assert [c[1] for c in calls] == [0, 1], (
            f"the same batch must be retried on the fresh process; calls={calls!r}"
        )
        assert calls[0][2] == calls[1][2], "retry must carry the same paths"
        assert len(rows) == 3
        assert all(_has_fake_date(r) for r in rows), f"rows lack EXIF: {rows!r}"

    def test_batch_failing_twice_is_recorded_and_later_batch_uses_rotated_proc(
        self, tmp_path, monkeypatch
    ):
        """Both attempts on the first 500-file batch time out: its rows still
        reach the manifest with no EXIF date, and the next batch succeeds on
        the process rotated in after the failed retry."""

        def _on_batch(call_no, _proc, _paths):
            if call_no in (1, 2):
                raise ExiftoolTimeout("read stalled")

        created, calls = _install_fake_exiftool(monkeypatch, _on_batch)

        rows, bus, finished = _run_scan(tmp_path, 501)

        assert finished, "scan must finish when every attempt on a batch fails"
        assert bus.failed_msgs == []
        assert len(rows) == 501, f"all 501 records must reach the manifest; got {len(rows)}"
        failed_batch = set(calls[0][2])
        dated = [r for r in rows if _has_fake_date(r)]
        undated = [r for r in rows if not _has_fake_date(r)]
        assert {r["source_path"] for r in undated} == {str(p) for p in failed_batch}
        assert len(dated) == 1, f"the later batch must succeed; dated={len(dated)}"
        assert calls[-1][1] == len(created) - 1, (
            "the later batch must run on the most recently rotated process"
        )

    def test_consumer_survives_full_queue_when_every_read_times_out(
        self, tmp_path, monkeypatch
    ):
        """Deadlock regression: every exiftool call times out and there are
        more exif-routed records than ``_EXIF_QUEUE_MAXSIZE`` (1000 at one
        worker). Before the fix the consumer died after its first batch and
        the HASH stage blocked forever on ``exif_queue.put``."""

        def _on_batch(_call_no, _proc, _paths):
            raise ExiftoolTimeout("read stalled")

        _install_fake_exiftool(monkeypatch, _on_batch)

        rows, _bus, finished = _run_scan(tmp_path, 1600, timeout=60.0)

        assert finished, "scan deadlocked: the exif consumer stopped draining"
        assert len(rows) == 1600

    def test_replacement_that_cannot_start_degrades_past_queue_capacity(
        self, tmp_path, monkeypatch
    ):
        """Degrade branch: the replacement exiftool cannot start, and there are
        more records than ``_EXIF_QUEUE_MAXSIZE``. The consumer must drain and
        discard, so producers never block; every row still reaches the manifest."""

        def _on_batch(_call_no, _proc, _paths):
            raise ExiftoolTimeout("read stalled")

        def _on_construct(attempt):
            if attempt == 2:
                raise OSError("exiftool.exe vanished")

        created, _calls = _install_fake_exiftool(
            monkeypatch, _on_batch, _on_construct
        )

        rows, _bus, finished = _run_scan(tmp_path, 1600, timeout=60.0)

        assert finished, "scan deadlocked after the replacement failed to start"
        assert len(created) == 1, f"no process may start after a failed rotation; got {len(created)}"
        assert len(rows) == 1600

    def test_non_timeout_batch_error_is_recorded_and_next_batch_works(
        self, tmp_path, monkeypatch
    ):
        """A non-timeout error from a batch: its rows are recorded signal-less,
        the process is rotated, and the next batch succeeds on the new one."""

        def _on_batch(call_no, _proc, _paths):
            if call_no == 1:
                raise ValueError("malformed exiftool output")

        created, calls = _install_fake_exiftool(monkeypatch, _on_batch)

        rows, bus, finished = _run_scan(tmp_path, 501)

        assert finished
        assert bus.failed_msgs == []
        assert len(created) == 2, f"expected one rotation; got {len(created)}"
        assert calls[1][1] == 1, "the next batch must run on the rotated process"
        assert len(rows) == 501
        assert sum(1 for r in rows if not _has_fake_date(r)) == 500
        assert sum(1 for r in rows if _has_fake_date(r)) == 1

    def test_cancel_kill_never_starts_a_replacement_process(
        self, tmp_path, monkeypatch
    ):
        """#561 — a cancel kills the live exiftool. The batch that then fails on
        the dead pipe must not rotate into a new process nothing will kill."""
        from core.app_service.cancel_token import _CancelToken

        token = _CancelToken()

        def _on_batch(_call_no, proc, _paths):
            token.request()
            for _ in range(500):  # wait (<= 5 s) for the cancel kill sweep
                if proc.killed:
                    break
                time.sleep(0.01)
            raise OSError(22, "Invalid argument")

        created, _calls = _install_fake_exiftool(monkeypatch, _on_batch)

        rows, bus, finished = _run_scan(tmp_path, 1100, token=token)

        assert finished, "cancelled scan must return"
        assert len(created) == 1, (
            f"cancel must not start a replacement process; got {len(created)}"
        )
        assert "Scan cancelled." in bus.failed_msgs

    def test_second_process_raising_runtime_error_does_not_hang(
        self, tmp_path, monkeypatch
    ):
        """A non-OSError from creating the replacement must degrade, not kill
        the consumer with items still in flight."""

        def _on_batch(_call_no, _proc, _paths):
            raise ExiftoolTimeout("read stalled")

        def _on_construct(attempt):
            if attempt == 2:
                raise RuntimeError("can't start new thread")

        created, _calls = _install_fake_exiftool(
            monkeypatch, _on_batch, _on_construct
        )

        rows, _bus, finished = _run_scan(tmp_path, 3)

        assert finished, "scan hung after a RuntimeError from the replacement"
        assert len(created) == 1
        assert len(rows) == 3

    def test_failure_after_sentinel_does_not_wait_for_a_second_sentinel(
        self, tmp_path, monkeypatch
    ):
        """The final flush (after the sentinel) raises from the bus. The consumer
        must exit instead of draining for a sentinel that never comes."""

        import core.app_service.scan_runner as _sr

        real_emit = _sr._emit_stage

        def _emit_raises_on_exif_progress(bus, tracker, completed, total, **kw):
            # Deterministic stand-in for a bus failure on the final exif flush
            # (the real emit is throttled, so it may never reach the bus).
            on_consumer = threading.current_thread().name.startswith("exif-consumer")
            if on_consumer and tracker.stage_name == "EXIFTOOL" and completed > 0:
                raise RuntimeError("bus emit failed")
            return real_emit(bus, tracker, completed, total, **kw)

        monkeypatch.setattr(_sr, "_emit_stage", _emit_raises_on_exif_progress)

        def _on_batch(_call_no, _proc, _paths):
            return None

        _install_fake_exiftool(monkeypatch, _on_batch)

        rows, _bus, finished = _run_scan(tmp_path, 3)

        assert finished, "consumer waited for a second sentinel after a crash"
        assert len(rows) == 3
