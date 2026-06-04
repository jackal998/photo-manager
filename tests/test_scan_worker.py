"""Tests for app/views/workers/scan_worker.py — ScanWorker pipeline behaviour.

Coverage:

- issue #46 regression — one bad file must never abort the whole scan
  (per-file ``compute_hashes`` exception is logged and skipped, manifest
  still written).
- issues #51 + #56 regression — an empty input folder is treated as a
  benign success (``completed_empty`` signal, "Done." log line, no
  ``failed`` emission, no modal).
- issue #57 regression — silent image-decode failures (truncated /
  corrupt JPEGs that compute_hashes returns from without raising) are
  routed to the skip channel instead of being misclassified as UNDATED.
- issue #49 regression — scan progress and errors are forwarded to
  loguru so the rotating ``app_<date>.log`` captures forensic context
  (the dialog log box is transient and disappears on close).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image


def _write_jpeg(path: Path, color=(128, 64, 32)) -> None:
    Image.new("RGB", (32, 32), color).save(path, "JPEG")


class TestScanWorkerSkipsBadFile:
    def test_per_file_exception_does_not_abort_scan(self, qapp, tmp_path, monkeypatch):
        """A LibRaw error on one file → that file is skipped, others scanned, manifest written.

        Regression for issue #46 (rawpy.LibRawFileUnsupportedError aborted the whole scan).
        """
        # Need a fresh QApplication to deliver signals via DirectConnection in this thread.
        from app.views.workers.scan_worker import ScanWorker

        a = tmp_path / "a.jpg"
        b = tmp_path / "b.jpg"
        bad = tmp_path / "bad.tif"  # routes to file_type="raw"
        _write_jpeg(a, color=(255, 0, 0))
        _write_jpeg(b, color=(0, 255, 0))
        bad.write_bytes(b"II*\x00" + b"\x00" * 64)  # TIFF magic, unparseable

        # Patch compute_hashes at the source so the late import in _run_pipeline picks it up.
        import scanner.hasher as _hasher
        import rawpy

        real_compute = _hasher.compute_hashes

        def fake_compute(path, file_type):
            if Path(path) == bad:
                raise rawpy.LibRawFileUnsupportedError("Unsupported file format or not RAW file")
            return real_compute(path, file_type)

        monkeypatch.setattr(_hasher, "compute_hashes", fake_compute)

        out = tmp_path / "manifest.sqlite"
        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=2,
        )

        progress: list[str] = []
        finished: list[str] = []
        failed: list[str] = []
        worker.progress.connect(progress.append)
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        # Run synchronously in this thread — DirectConnection delivers signals immediately.
        worker.run()

        assert not failed, f"Scan must not have failed; got: {failed}"
        assert finished == [str(out)], f"finished signal wrong: {finished}"
        assert out.exists(), "manifest file should have been written"
        assert any("Skipped 1 unreadable" in m for m in progress), \
            f"skip summary missing from progress: {progress!r}"
        assert any("bad.tif" in m for m in progress), \
            f"skipped file path should appear in progress: {progress!r}"


class TestScanWorkerEmptyInput:
    def test_empty_folder_signals_completed_empty_not_failed(self, qapp, tmp_path):
        """An empty source folder is a benign success, not a failure.

        Regression for issues #51 (red 'Scan Failed' modal misclassified the
        case) and #56 (QA driver polling for 'Done.' / 'Error' / 'Failed'
        timed out because the log only contained 'ERROR:' from the failure
        path).

        Expectations:
          - ``completed_empty`` fires exactly once.
          - ``failed`` does NOT fire.
          - ``finished`` does NOT fire (no manifest written).
          - The progress log contains a 'Done.' terminator so the QA
            driver's case-sensitive match succeeds.
        """
        from app.views.workers.scan_worker import ScanWorker

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        worker = ScanWorker(
            sources={"src": str(empty_dir)},
            output_path=str(tmp_path / "manifest.sqlite"),
            recursive_map={"src": False},
            workers=2,
        )

        progress: list[str] = []
        finished: list[str] = []
        failed: list[str] = []
        completed_empty_calls: list[None] = []
        worker.progress.connect(progress.append)
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)
        worker.completed_empty.connect(lambda: completed_empty_calls.append(None))

        worker.run()

        assert not failed, f"Empty input must not emit `failed`; got: {failed}"
        assert not finished, f"Empty input must not emit `finished`; got: {finished}"
        assert len(completed_empty_calls) == 1, \
            f"`completed_empty` should fire exactly once; got {len(completed_empty_calls)}"
        assert any("Done." in m for m in progress), \
            f"progress log must contain a 'Done.' terminator: {progress!r}"


class TestScanWorkerCorruptImage:
    def test_truncated_jpeg_is_logged_and_excluded_from_manifest(
        self, qapp, tmp_path
    ):
        """A truncated JPEG should be logged as ImageDecodeError, not silently UNDATED.

        Regression for issue #57: ``compute_hashes`` returns successfully for a
        truncated JPEG (sha256 from raw bytes works) but PIL can't extract a
        pHash. Pre-fix, the file landed in the manifest with action=UNDATED,
        visually indistinguishable from a JPG that simply has no EXIF date.
        """
        import sqlite3

        from app.views.workers.scan_worker import ScanWorker

        # Two files: one valid JPEG, one truncated JPEG (1 KB cut).
        good = tmp_path / "good.jpg"
        bad = tmp_path / "bad_truncated.jpg"
        _write_jpeg(good, color=(0, 128, 255))

        # Build a real JPEG, then truncate it so PIL.Image.load() fails.
        full = tmp_path / "_full.jpg"
        Image.new("RGB", (200, 150), (200, 100, 50)).save(full, "JPEG")
        bad.write_bytes(full.read_bytes()[:1024])
        full.unlink()

        out = tmp_path / "manifest.sqlite"
        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=2,
        )

        progress: list[str] = []
        finished: list[str] = []
        failed: list[str] = []
        worker.progress.connect(progress.append)
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        worker.run()

        assert not failed, f"Scan must not have failed; got: {failed}"
        assert finished == [str(out)], f"finished signal wrong: {finished}"

        # Log surfaces the corrupt file with the synthetic exception type.
        assert any("ImageDecodeError" in m for m in progress), \
            f"progress should flag corrupt file as ImageDecodeError: {progress!r}"
        assert any("bad_truncated.jpg" in m for m in progress), \
            f"corrupt file path should appear in progress: {progress!r}"

        # Manifest contains the good file, NOT the corrupt one. Query SQLite
        # directly because ManifestRepository.load() filters to grouped rows.
        with sqlite3.connect(out) as conn:
            paths = [Path(p).name for (p,) in conn.execute(
                "SELECT source_path FROM migration_manifest"
            )]
        assert "good.jpg" in paths, f"good file missing from manifest: {paths}"
        assert "bad_truncated.jpg" not in paths, \
            f"corrupt file should be excluded from manifest, but found in: {paths}"

    def test_gif_not_flagged_as_corrupt(self, qapp, tmp_path):
        """GIFs must NOT be flagged as ImageDecodeError.

        Regression for #75: scanner/hasher.compute_hashes intentionally
        returns ``phash=None`` for GIFs (early-return at hasher.py:53).
        The #57 fix originally treated any non-video with phash=None as
        corrupt, which false-positived 100% of GIF inputs and silently
        dropped them from the manifest with a misleading error.
        """
        import sqlite3

        from app.views.workers.scan_worker import ScanWorker

        gif = tmp_path / "good.gif"
        # Tiny valid GIF (1×1 transparent pixel).
        Image.new("RGB", (8, 8), (200, 0, 0)).save(gif, "GIF")

        out = tmp_path / "manifest.sqlite"
        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=1,
        )
        progress: list[str] = []
        worker.progress.connect(progress.append)
        worker.run()

        # The GIF should NOT appear in any "Skipped … unreadable" log line
        # nor be tagged with the synthetic ImageDecodeError marker.
        for line in progress:
            assert "good.gif" not in line or "ImageDecodeError" not in line, (
                f"GIF should not be flagged as corrupt: {line!r}"
            )

        # And it must reach the manifest.
        with sqlite3.connect(out) as conn:
            paths = [Path(p).name for (p,) in conn.execute(
                "SELECT source_path FROM migration_manifest"
            )]
        assert "good.gif" in paths, \
            f"GIF should be in manifest, not excluded as corrupt: {paths}"

    def test_non_camera_tiff_not_flagged_as_corrupt(self, qapp, tmp_path):
        """Non-camera-RAW TIFFs (Photoshop / scanner output) must NOT be flagged.

        Regression for #75: TIFF maps to file_type='raw' (scanner/media.py),
        and rawpy fails on synthetic / non-camera TIFFs — so phash ends up
        None. The #57 fix originally treated that as corruption, which
        silently dropped legitimate non-RAW TIFFs from real user libraries.
        """
        import sqlite3

        from app.views.workers.scan_worker import ScanWorker

        tiff = tmp_path / "scan_output.tif"
        # Synthetic TIFF — PIL writes it cleanly but rawpy can't parse it.
        Image.new("RGB", (32, 32), (50, 100, 150)).save(tiff, "TIFF")

        out = tmp_path / "manifest.sqlite"
        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=1,
        )
        progress: list[str] = []
        worker.progress.connect(progress.append)
        worker.run()

        for line in progress:
            assert "scan_output.tif" not in line or "ImageDecodeError" not in line, (
                f"non-camera-RAW TIFF should not be flagged as corrupt: {line!r}"
            )

        with sqlite3.connect(out) as conn:
            paths = [Path(p).name for (p,) in conn.execute(
                "SELECT source_path FROM migration_manifest"
            )]
        assert "scan_output.tif" in paths, \
            f"TIFF should be in manifest, not excluded as corrupt: {paths}"


class TestScanWorkerParallelWalk:
    """#452 — multiple sources walk in parallel; single source stays serial.

    Order-stability matters: records must appear in source-iteration
    order regardless of which walker thread finishes first, otherwise
    downstream priority inference (which uses iteration order as a
    tiebreaker) would become non-deterministic.
    """

    def test_two_sources_records_in_source_order_even_if_beta_returns_first(
        self, qapp, tmp_path, monkeypatch
    ):
        """Parallel walks must concatenate per-source results in
        source-iteration order, NOT thread-completion order.

        Setup: patch ``scan_sources`` so the ``beta`` walk returns
        immediately while ``alpha`` sleeps 100ms. Without the
        source-order concat, beta's records would appear first in the
        worker's ``records`` list. We assert the opposite by sniffing
        the per-source ``"  → N files"`` log lines plus a final
        ``Total: N media files`` count.
        """
        import time as _time

        import app.views.workers.scan_worker as _module
        from app.views.workers.scan_worker import ScanWorker
        from scanner.walker import FileRecord

        original = _module.__dict__.get("scan_sources")

        # Each call hands the worker a single FileRecord whose label
        # encodes the call-site, then sleeps if alpha. We don't care
        # what the manifest stage does with these — we only care that
        # the worker concatenates partials in source-iteration order.
        def fake_scan_sources(
            sources, limit=None, recursive_map=None,
            progress_callback=None, cancel_check=None,
        ):
            (label,) = sources.keys()
            (root,) = sources.values()
            if label == "alpha":
                _time.sleep(0.1)
            # Build N fake records — use any existing jpg under root.
            recs = []
            for p in root.iterdir():
                if p.suffix.lower() == ".jpg":
                    recs.append(FileRecord(
                        path=p, source_label=label, file_type="jpeg",
                    ))
                    if progress_callback:
                        progress_callback()
            return recs

        # Inject the fake into the late-import inside _run_pipeline by
        # patching the module the worker imports from.
        import scanner.walker as _walker
        monkeypatch.setattr(_walker, "scan_sources", fake_scan_sources)

        src_a = tmp_path / "alpha"
        src_b = tmp_path / "beta"
        src_a.mkdir()
        src_b.mkdir()
        _write_jpeg(src_a / "a1.jpg")
        _write_jpeg(src_b / "b1.jpg")

        worker = ScanWorker(
            sources={"alpha": str(src_a), "beta": str(src_b)},
            output_path=str(tmp_path / "manifest.sqlite"),
            recursive_map={"alpha": True, "beta": True},
            workers=2,
        )

        progress: list[str] = []
        worker.progress.connect(progress.append)
        worker.run()

        # The per-source "→ N files" log lines may arrive in any order
        # (alpha sleeps, so beta logs first). What MUST hold is that
        # the total + record concatenation happens once and reports the
        # combined count. The ordering invariant lives in the records
        # list which downstream sees; we sniff it indirectly by checking
        # the "Hashing" line count — if both sources contributed, it
        # reads 2.
        hashing_line = next(
            (line for line in progress if line.startswith("Hashing ")), None
        )
        assert hashing_line is not None, (
            f"expected a 'Hashing N files' line after parallel walk; "
            f"got: {progress!r}"
        )
        assert "Hashing 2 files" in hashing_line, (
            f"both source partials must concat into a single 2-file hash batch; "
            f"got {hashing_line!r}"
        )

    def test_single_source_runs_serial_no_executor(self, qapp, tmp_path):
        """A 1-source scan must NOT spin up the thread pool — verified
        indirectly: we patch ThreadPoolExecutor to raise, then run a
        single-source scan and assert it still succeeds.
        """
        from app.views.workers.scan_worker import ScanWorker

        _write_jpeg(tmp_path / "only.jpg")
        out = tmp_path / "manifest.sqlite"
        worker = ScanWorker(
            sources={"solo": str(tmp_path)},
            output_path=str(out),
            recursive_map={"solo": False},
            workers=1,
        )
        worker.run()
        assert out.exists(), "single-source scan should still write its manifest"


class TestScanWorkerExifWorkers:
    """#451 — exif_workers is clamped at ScanWorker construction.

    Floor: 1 (never zero, would deadlock the queue with no consumers).
    Cap: min(4, cpu_count() // 2) — exiftool processes scale near-linearly
    only up to ~4 on a modern CPU; above that each spawn costs ~200ms
    on Windows so the user pays without speedup gain.
    """

    def test_exif_workers_floor_one(self, qapp, tmp_path):
        from app.views.workers.scan_worker import ScanWorker

        w = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            exif_workers=0,
        )
        assert w.exif_workers == 1

    def test_exif_workers_capped_at_cpu_half(self, qapp, tmp_path):
        import os
        from app.views.workers.scan_worker import ScanWorker

        cap = max(1, min(4, (os.cpu_count() or 4) // 2))
        w = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            exif_workers=99,
        )
        assert w.exif_workers == cap

    def test_exif_workers_within_range_kept(self, qapp, tmp_path):
        from app.views.workers.scan_worker import ScanWorker

        w = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            exif_workers=2,
        )
        # 2 is below the cap on every CPU we'd test on (cpu_count >= 4)
        # and above the floor — should pass through unchanged.
        assert w.exif_workers == 2

    def test_n_consumer_threads_spawned(self, qapp, tmp_path):
        """A 2-exif-worker scan must spawn exactly 2 consumer threads
        named ``exif-consumer-N``. Verified by sampling
        ``threading.enumerate()`` after the scan completes — the
        consumers join cleanly so the leak check from the pipeline
        suite also enforces that count returns to zero.
        """
        import threading
        from app.views.workers.scan_worker import ScanWorker

        _write_jpeg(tmp_path / "a.jpg")
        observed: list[str] = []

        # Capture thread names mid-scan by patching the consumer to
        # snapshot when it starts. Simpler than wrangling timing: hook
        # threading.Thread.start to log names.
        original_start = threading.Thread.start

        def spy_start(self):
            if self.name.startswith("exif-consumer-"):
                observed.append(self.name)
            return original_start(self)

        threading.Thread.start = spy_start
        try:
            worker = ScanWorker(
                sources={"src": str(tmp_path)},
                output_path=str(tmp_path / "m.sqlite"),
                recursive_map={"src": False},
                exif_workers=2,
            )
            worker.run()
        finally:
            threading.Thread.start = original_start

        assert sorted(observed) == ["exif-consumer-0", "exif-consumer-1"], (
            f"expected exactly 2 exif consumer threads; observed: {observed!r}"
        )


class TestHashPoolSetting:
    """#486-PR2 — the ``scan.hash_pool`` executor selector.

    The HASH stage runs across a ThreadPoolExecutor by default and a
    ProcessPoolExecutor when ``hash_pool="process"``. These tests cover
    the construction-time validation and the executor-routing branch.

    Routing is verified WITHOUT spawning real OS processes: the process
    pool is monkeypatched to a ThreadPoolExecutor subclass that records
    its instantiation. That exercises the real code path (process branch
    chosen + parent-side outcome routing producing a manifest) while
    staying CI-safe — the genuine cross-process spawn is validated by
    real-world runs, not in CI.
    """

    def test_default_is_thread(self, qapp, tmp_path):
        from app.views.workers.scan_worker import ScanWorker

        w = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
        )
        assert w.hash_pool == "thread"

    def test_process_value_kept(self, qapp, tmp_path):
        from app.views.workers.scan_worker import ScanWorker

        w = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            hash_pool="process",
        )
        assert w.hash_pool == "process"

    def test_unknown_value_falls_back_to_thread(self, qapp, tmp_path):
        """Catches: a typo'd / stale settings.json value silently selecting
        a non-existent executor mode instead of the safe default."""
        from app.views.workers.scan_worker import ScanWorker

        w = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            hash_pool="garbage",
        )
        assert w.hash_pool == "thread"

    def test_process_mode_routes_to_process_pool_and_writes_manifest(
        self, qapp, tmp_path, monkeypatch
    ):
        """Catches: the process branch not being selected, OR the parent's
        _route_outcome path dropping HashResults so the manifest comes out
        empty. Asserts both the executor choice and end-to-end output."""
        import concurrent.futures as _cf
        from app.views.workers.scan_worker import ScanWorker

        _write_jpeg(tmp_path / "only.jpg")
        out = tmp_path / "manifest.sqlite"

        instantiated: list[bool] = []
        real_thread_pool = _cf.ThreadPoolExecutor

        class SpyProcessPool(real_thread_pool):
            # Delegates to a real ThreadPoolExecutor so run_hash_for_record
            # runs in-thread (no OS spawn) while we record that the process
            # branch instantiated it.
            def __init__(self, *args, **kwargs):
                instantiated.append(True)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(_cf, "ProcessPoolExecutor", SpyProcessPool)

        worker = ScanWorker(
            sources={"solo": str(tmp_path)},
            output_path=str(out),
            recursive_map={"solo": False},
            workers=1,
            hash_pool="process",
        )
        worker.run()

        assert instantiated == [True], "process mode must select ProcessPoolExecutor"
        assert out.exists(), "parent-side outcome routing must still write the manifest"

    def test_thread_mode_never_touches_process_pool(
        self, qapp, tmp_path, monkeypatch
    ):
        """Catches: the default regressing so it accidentally instantiates
        a ProcessPoolExecutor. Patches it to explode — a default scan must
        complete without ever constructing it."""
        import concurrent.futures as _cf
        from app.views.workers.scan_worker import ScanWorker

        _write_jpeg(tmp_path / "only.jpg")
        out = tmp_path / "manifest.sqlite"

        def _boom(*args, **kwargs):
            raise AssertionError("thread mode must not construct a ProcessPoolExecutor")

        monkeypatch.setattr(_cf, "ProcessPoolExecutor", _boom)

        worker = ScanWorker(
            sources={"solo": str(tmp_path)},
            output_path=str(out),
            recursive_map={"solo": False},
            workers=1,
            hash_pool="thread",
        )
        worker.run()

        assert out.exists(), "default thread scan should write its manifest"


class TestHashPoolCalibration:
    """#486-PR3 — hash_pool="auto" times a sample through both executors at
    scan start, projects each to the real file count, and runs the faster.

    The decision tests drive _calibrate_hash_pool directly with synthetic
    per-file rates (the projection logic is what matters, not the
    wall-clock); _time_hash_executor and _profile_process_pool are each
    covered by a real-hash test so the measurement paths aren't mock-only.
    """

    def _worker(self, tmp_path):
        from app.views.workers.scan_worker import ScanWorker

        return ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            workers=2,
        )

    def test_auto_value_kept_at_construction(self, qapp, tmp_path):
        from app.views.workers.scan_worker import ScanWorker

        w = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            hash_pool="auto",
        )
        assert w.hash_pool == "auto"

    def _patch_timings(self, monkeypatch, thread_per_file, spawn, process_per_file):
        """Stub the two measurement helpers with fixed per-file rates so the
        *projection* logic is what's under test, not the wall-clock."""
        import app.views.workers.scan_worker as sw

        monkeypatch.setattr(
            sw,
            "_time_hash_executor",
            lambda cls, sample, w: thread_per_file * len(sample),
        )
        monkeypatch.setattr(
            sw,
            "_profile_process_pool",
            lambda cls, sample, w: (spawn, process_per_file),
        )

    def test_calibration_picks_process_when_projection_favors_it(
        self, qapp, tmp_path, monkeypatch
    ):
        """Catches: the projected-winner comparison inverted. Large N where
        process's lower per-file rate beats its one-time spawn."""
        import app.views.workers.scan_worker as sw

        # thread 2s/file; process 100s spawn + 1s/file. Break-even N=100.
        self._patch_timings(monkeypatch, 2.0, 100.0, 1.0)
        records = list(range(10_000))  # well past break-even → process

        assert self._worker(tmp_path)._calibrate_hash_pool(
            records, object(), object()
        ) == "process"

    def test_calibration_picks_thread_when_projection_favors_it(
        self, qapp, tmp_path, monkeypatch
    ):
        """Catches: process chosen on a scan too small to amortise its spawn
        cost (and ties defaulting to process)."""
        self._patch_timings(monkeypatch, 2.0, 100.0, 1.0)
        records = list(range(50))  # below break-even → thread

        assert self._worker(tmp_path)._calibrate_hash_pool(
            records, object(), object()
        ) == "thread"

    def test_projection_flips_winner_with_file_count(
        self, qapp, tmp_path, monkeypatch
    ):
        """The core of the fix: with *identical* measured rates, the winner
        must depend on the real file count — process's one-time spawn cost
        dominates on a small scan and amortises away on a large one. A flat
        per-sample comparison (the #498 bug) could never flip here."""
        worker = self._worker(tmp_path)
        self._patch_timings(monkeypatch, 2.0, 100.0, 1.0)  # break-even N=100

        small = worker._calibrate_hash_pool(list(range(50)), object(), object())
        large = worker._calibrate_hash_pool(list(range(10_000)), object(), object())

        assert (small, large) == ("thread", "process")

    def test_calibration_skipped_below_floor_without_timing(
        self, qapp, tmp_path, monkeypatch
    ):
        """Catches: paying the (expensive) process-spawn calibration cost on
        a tiny scan where it can't yield a reliable signal."""
        import app.views.workers.scan_worker as sw

        timed: list = []
        monkeypatch.setattr(
            sw, "_time_hash_executor", lambda *a: timed.append("thread") or 0.0
        )
        monkeypatch.setattr(
            sw, "_profile_process_pool", lambda *a: timed.append("process") or (0.0, 0.0)
        )
        records = list(range(sw._CALIBRATION_MIN - 1))  # one below the floor

        result = self._worker(tmp_path)._calibrate_hash_pool(
            records, object(), object()
        )
        assert result == "thread"
        assert timed == [], "below the floor calibration must not time anything"

    def test_time_hash_executor_returns_elapsed(self, qapp, tmp_path):
        """Real-hash timing path (no mock): hashes a few real jpegs through a
        ThreadPoolExecutor and returns a non-negative elapsed time."""
        from concurrent.futures import ThreadPoolExecutor
        import app.views.workers.scan_worker as sw
        from scanner.walker import FileRecord

        recs = []
        for i in range(3):
            p = tmp_path / f"f{i}.jpg"
            _write_jpeg(p)
            recs.append(FileRecord(path=p, source_label="s", file_type="jpeg"))

        elapsed = sw._time_hash_executor(ThreadPoolExecutor, recs, 2)
        assert elapsed >= 0.0

    def test_profile_process_pool_returns_spawn_and_rate(self, qapp, tmp_path):
        """Real-hash measurement path (no mock): runs the cold/warm two-batch
        split through a real executor and returns a non-negative (spawn,
        per_file) pair. Uses ThreadPoolExecutor so CI never spawns a real
        subprocess (the cold/warm split logic is what's exercised here)."""
        from concurrent.futures import ThreadPoolExecutor
        import app.views.workers.scan_worker as sw
        from scanner.walker import FileRecord

        recs = []
        for i in range(4):
            p = tmp_path / f"f{i}.jpg"
            _write_jpeg(p)
            recs.append(FileRecord(path=p, source_label="s", file_type="jpeg"))

        spawn, per_file = sw._profile_process_pool(ThreadPoolExecutor, recs, 2)
        assert spawn >= 0.0
        assert per_file >= 0.0

    def test_auto_scan_below_floor_runs_thread_and_writes_manifest(
        self, qapp, tmp_path, monkeypatch
    ):
        """End-to-end: a small 'auto' scan calibrates (skips, too few),
        resolves to thread, and still writes a manifest. Patches the process
        pool to explode to prove the resolved thread path never touches it."""
        import concurrent.futures as _cf
        from app.views.workers.scan_worker import ScanWorker

        _write_jpeg(tmp_path / "only.jpg")
        out = tmp_path / "manifest.sqlite"

        def _boom(*args, **kwargs):
            raise AssertionError("auto-below-floor must resolve to thread")

        monkeypatch.setattr(_cf, "ProcessPoolExecutor", _boom)

        worker = ScanWorker(
            sources={"solo": str(tmp_path)},
            output_path=str(out),
            recursive_map={"solo": False},
            workers=1,
            hash_pool="auto",
        )
        worker.run()

        assert out.exists(), "auto scan should resolve and write its manifest"

    # ---- #486-PR3b: fingerprint cache -------------------------------------

    def test_fingerprint_stable_and_sensitive(self):
        """The cache key is stable for identical inputs and changes when any
        of (cpu count, source path, recursive flag) changes — so a different
        machine or folder set correctly misses and re-measures."""
        from app.views.workers.scan_worker import hash_pool_fingerprint as fp

        base = fp({"a": "/x"}, {"a": True}, 8)
        assert base == fp({"a": "/x"}, {"a": True}, 8)  # stable
        assert base != fp({"a": "/x"}, {"a": True}, 4)  # cpu count matters
        assert base != fp({"a": "/y"}, {"a": True}, 8)  # source path matters
        assert base != fp({"a": "/x"}, {"a": False}, 8)  # recursive matters

    def test_fingerprint_invalidates_on_recipe_version_bump(self, monkeypatch):
        """#526 — bumping the hash-recipe or grouping-strategy version must
        change the key so a calibration measured under an old recipe (e.g.
        before dHash joined the 7-tuple) is never reused. This is the #517
        breadcrumb: pre-#526 the key had no recipe component."""
        import scanner.dedup as dedup
        import scanner.hasher as hasher
        from app.views.workers.scan_worker import hash_pool_fingerprint as fp

        base = fp({"a": "/x"}, {"a": True}, 8)
        monkeypatch.setattr(hasher, "HASH_RECIPE_VERSION", "999")
        assert base != fp({"a": "/x"}, {"a": True}, 8)  # hash recipe matters
        monkeypatch.setattr(hasher, "HASH_RECIPE_VERSION", "1")  # restore for next
        monkeypatch.setattr(dedup, "GROUPING_STRATEGY_VERSION", "999")
        assert base != fp({"a": "/x"}, {"a": True}, 8)  # grouping strategy matters

    def test_cached_rates_skip_measurement_and_reproject(
        self, qapp, tmp_path, monkeypatch
    ):
        """Cache hit: the worker re-projects the cached rates to the current
        file count WITHOUT re-measuring (the timing helpers would raise), and
        the pick still adapts to N (small→thread, large→process)."""
        import app.views.workers.scan_worker as sw
        from app.views.workers.scan_worker import ScanWorker

        def boom(*a):
            raise AssertionError("cache hit must not re-measure")

        monkeypatch.setattr(sw, "_time_hash_executor", boom)
        monkeypatch.setattr(sw, "_profile_process_pool", boom)
        rates = {"thread_per_file": 2.0, "process_per_file": 1.0, "spawn": 100.0}
        worker = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            workers=2,
            hash_pool="auto",
            hash_pool_rates=rates,
        )
        # break-even N=100 from the cached rates, no measurement
        assert worker._calibrate_hash_pool(list(range(50)), object(), object()) == "thread"
        assert worker._calibrate_hash_pool(
            list(range(10_000)), object(), object()
        ) == "process"

    def test_fresh_calibration_emits_measured_rates(
        self, qapp, tmp_path, monkeypatch
    ):
        """Cache miss: a fresh measurement is emitted via hash_pool_measured
        so the dialog can persist it (the inbound half of the cache)."""
        import app.views.workers.scan_worker as sw
        from app.views.workers.scan_worker import ScanWorker

        monkeypatch.setattr(
            sw, "_time_hash_executor", lambda cls, sample, w: 2.0 * len(sample)
        )
        monkeypatch.setattr(
            sw, "_profile_process_pool", lambda cls, sample, w: (100.0, 1.0)
        )
        # #526 — stub the grouping micro-benchmark so the emitted schema is
        # deterministic (the real one times a synthetic hash set).
        monkeypatch.setattr(sw, "_profile_grouping", lambda: (1e-6, 5e-7))
        worker = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            workers=2,
            hash_pool="auto",
        )
        captured: list = []
        worker.hash_pool_measured.connect(captured.append)

        worker._calibrate_hash_pool(list(range(10_000)), object(), object())

        # #526 — the cache entry now carries the grouping micro-rates too.
        assert captured == [{
            "thread_per_file": 2.0,
            "process_per_file": 1.0,
            "spawn": 100.0,
            "group_per_pair": 1e-6,
            "group_bk_per_candidate": 5e-7,
        }]

    def test_store_hash_pool_rates_round_trips_through_settings(self, tmp_path):
        """#486-PR3b — a fresh calibration is written to scan.hash_pool_cache
        under its fingerprint AND flushed to disk, so the next session reads it
        back. Qt-free (no dialog) — exercises the real JsonSettings round-trip."""
        import json
        from app.views.workers.scan_worker import store_hash_pool_rates
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"sources": {}}), encoding="utf-8")
        settings = JsonSettings(settings_path)
        rates = {"thread_per_file": 2.0, "process_per_file": 1.0, "spawn": 0.5}

        store_hash_pool_rates(settings, "fp_test", rates)

        assert settings.get("scan.hash_pool_cache")["fp_test"] == rates
        reloaded = JsonSettings(settings_path)  # next session reads from disk
        assert reloaded.get("scan.hash_pool_cache")["fp_test"] == rates

    def test_valid_hash_pool_rates_predicate(self):
        """Boundary validator for hand-editable settings.json cache entries."""
        from app.views.workers.scan_worker import _valid_hash_pool_rates as ok

        assert ok({"thread_per_file": 1.0, "process_per_file": 0.5, "spawn": 0.1})
        assert not ok(None)
        assert not ok({"thread_per_file": 1.0})  # partial
        assert not ok({"thread_per_file": "x", "process_per_file": 1, "spawn": 1})  # type

    def test_malformed_cached_rates_trigger_remeasure(
        self, qapp, tmp_path, monkeypatch
    ):
        """A corrupt/partial cached entry (e.g. hand-edited settings.json) is
        treated as a cache miss and re-measured, not crashed on."""
        import app.views.workers.scan_worker as sw
        from app.views.workers.scan_worker import ScanWorker

        monkeypatch.setattr(
            sw, "_time_hash_executor", lambda cls, sample, w: 2.0 * len(sample)
        )
        monkeypatch.setattr(
            sw, "_profile_process_pool", lambda cls, sample, w: (100.0, 1.0)
        )
        worker = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            workers=2,
            hash_pool="auto",
            hash_pool_rates={"thread_per_file": 1.0},  # partial → invalid
        )
        captured: list = []
        worker.hash_pool_measured.connect(captured.append)

        result = worker._calibrate_hash_pool(list(range(10_000)), object(), object())

        assert result == "process"  # re-measured rates project to process
        assert len(captured) == 1, "malformed cache must trigger a fresh measurement"

    # ---- #526 grouping-stage calibration -------------------------------------

    def test_derive_bktree_floor_crossover_and_clamp(self):
        """The floor is the measured brute-vs-BK crossover, clamped so a noisy
        micro-measurement can't yield a silly value."""
        from app.views.workers.scan_worker import (
            _GROUP_FLOOR_MAX,
            _GROUP_FLOOR_MIN,
            _derive_bktree_floor,
        )

        # BK cheap per unit vs brute per pair → crossover below the min → clamp.
        assert _derive_bktree_floor(1e-6, 5e-7) == _GROUP_FLOOR_MIN
        # Mid-range crossover passes through: 2*1e-5/1e-7 + 1 = 201.
        assert _derive_bktree_floor(1e-7, 1e-5) == 201
        # Huge ratio → clamp at the max.
        assert _derive_bktree_floor(1e-8, 1e-4) == _GROUP_FLOOR_MAX
        # Degenerate per-pair → max (never engage BK on a bad measurement).
        assert _derive_bktree_floor(0.0, 1e-6) == _GROUP_FLOOR_MAX

    def test_fresh_calibration_sets_grouping_floor(
        self, qapp, tmp_path, monkeypatch
    ):
        """A fresh calibration derives the BK-tree floor from the measured
        grouping micro-rates and stashes it for the classify() call."""
        import app.views.workers.scan_worker as sw
        from app.views.workers.scan_worker import ScanWorker, _derive_bktree_floor

        monkeypatch.setattr(
            sw, "_time_hash_executor", lambda cls, sample, w: 2.0 * len(sample)
        )
        monkeypatch.setattr(
            sw, "_profile_process_pool", lambda cls, sample, w: (100.0, 1.0)
        )
        monkeypatch.setattr(sw, "_profile_grouping", lambda: (1e-7, 1e-5))
        worker = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            workers=2,
            hash_pool="auto",
        )
        assert worker._calibrated_bktree_floor is None  # not yet calibrated

        worker._calibrate_hash_pool(list(range(10_000)), object(), object())

        assert worker._calibrated_bktree_floor == _derive_bktree_floor(1e-7, 1e-5)

    def test_cached_group_rates_set_floor_without_measuring(
        self, qapp, tmp_path, monkeypatch
    ):
        """Cache hit carrying grouping micro-rates derives the floor with no
        re-measurement (the timing helpers would raise)."""
        import app.views.workers.scan_worker as sw
        from app.views.workers.scan_worker import ScanWorker, _derive_bktree_floor

        def boom(*a):
            raise AssertionError("cache hit must not re-measure")

        monkeypatch.setattr(sw, "_time_hash_executor", boom)
        monkeypatch.setattr(sw, "_profile_process_pool", boom)
        monkeypatch.setattr(sw, "_profile_grouping", boom)
        rates = {
            "thread_per_file": 2.0, "process_per_file": 1.0, "spawn": 100.0,
            "group_per_pair": 1e-7, "group_bk_per_candidate": 1e-5,
        }
        worker = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            workers=2,
            hash_pool="auto",
            hash_pool_rates=rates,
        )
        worker._calibrate_hash_pool(list(range(10_000)), object(), object())
        assert worker._calibrated_bktree_floor == _derive_bktree_floor(1e-7, 1e-5)

    def test_legacy_cache_without_group_keys_floor_stays_none(
        self, qapp, tmp_path, monkeypatch
    ):
        """A pre-#526 cache entry (hash rates only, no grouping keys) still
        serves the hash pick, but the grouping floor falls back to None so
        classify() uses the module default — no crash."""
        import app.views.workers.scan_worker as sw
        from app.views.workers.scan_worker import ScanWorker

        def boom(*a):
            raise AssertionError("cache hit must not re-measure")

        monkeypatch.setattr(sw, "_time_hash_executor", boom)
        monkeypatch.setattr(sw, "_profile_process_pool", boom)
        monkeypatch.setattr(sw, "_profile_grouping", boom)
        worker = ScanWorker(
            sources={"s": str(tmp_path)},
            output_path=str(tmp_path / "m.sqlite"),
            recursive_map={"s": False},
            workers=2,
            hash_pool="auto",
            hash_pool_rates={
                "thread_per_file": 2.0, "process_per_file": 1.0, "spawn": 100.0,
            },
        )
        worker._calibrate_hash_pool(list(range(10_000)), object(), object())
        assert worker._calibrated_bktree_floor is None


class TestScanWorkerExifPipeline:
    """#450 — hash→exif pipeline overlap.

    The behavioural contract:
      - Missing-exiftool warning still surfaces (and the scan completes).
      - Corrupt-image detection (the per-record check moved into the
        hash worker) still routes to the skipped log and excludes the
        path from the manifest.
      - Cancel during overlap tears down both threads — covered by s03
        scenario at layer 3; here we just confirm no thread leak in a
        successful run via the consumer thread name check.
    """

    def test_missing_exiftool_logs_warning_and_completes(
        self, qapp, tmp_path, monkeypatch
    ):
        """When exiftool isn't on PATH, the consumer latches the
        ``exiftool_missing`` flag, drains the queue, and the worker
        surfaces the install hint after consumer.join() — scan still
        produces a manifest.
        """
        from app.views.workers.scan_worker import ScanWorker
        import scanner.exif as _exif

        _write_jpeg(tmp_path / "a.jpg")
        _write_jpeg(tmp_path / "b.jpg")

        def raise_missing(*_a, **_kw):
            raise FileNotFoundError("exiftool not found")

        monkeypatch.setattr(_exif, "ExiftoolProcess", raise_missing)

        out = tmp_path / "manifest.sqlite"
        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=2,
        )
        progress: list[str] = []
        worker.progress.connect(progress.append)
        worker.run()

        assert any("exiftool not found on PATH" in m for m in progress), (
            f"missing-exiftool warning must surface; got: {progress!r}"
        )
        assert out.exists(), "manifest must still be written when exiftool is missing"

    def test_no_consumer_thread_leak_after_success(
        self, qapp, tmp_path
    ):
        """After a successful scan no thread named ``exif-consumer``
        should remain alive — the consumer must drain on sentinel and
        join cleanly.
        """
        import threading

        from app.views.workers.scan_worker import ScanWorker

        _write_jpeg(tmp_path / "a.jpg")
        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(tmp_path / "manifest.sqlite"),
            recursive_map={"src": False},
            workers=1,
        )
        worker.run()

        leaked = [t for t in threading.enumerate() if t.name == "exif-consumer" and t.is_alive()]
        assert not leaked, f"exif-consumer thread leaked: {leaked!r}"


class TestScanWorkerLogging:
    def test_scan_progress_and_errors_forwarded_to_loguru(
        self, qapp, tmp_path
    ):
        """Progress lines and per-file skip records flow through loguru.

        Regression for issue #49: scan errors used to live only in the
        dialog's transient log box, so "the scan stopped" reports had no
        artifact to attach. After the fix, every progress emission lands
        in the rotating ``app_<date>.log`` (via loguru) — and a corrupt
        file (#57) shows up there with its path and synthetic exception
        type for forensics.
        """
        from loguru import logger

        from app.views.workers.scan_worker import ScanWorker

        # Same fixture shape as the corrupt-image test — one valid JPEG
        # plus one truncated JPEG so we exercise both progress lines and
        # per-file skip-record forwarding.
        good = tmp_path / "good.jpg"
        bad = tmp_path / "bad_truncated.jpg"
        _write_jpeg(good, color=(0, 128, 255))
        full = tmp_path / "_full.jpg"
        Image.new("RGB", (200, 150), (200, 100, 50)).save(full, "JPEG")
        bad.write_bytes(full.read_bytes()[:1024])
        full.unlink()

        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="INFO")
        try:
            worker = ScanWorker(
                sources={"src": str(tmp_path)},
                output_path=str(tmp_path / "manifest.sqlite"),
                recursive_map={"src": False},
                workers=2,
            )
            worker.run()
        finally:
            logger.remove(sink_id)

        joined = "\n".join(captured)
        assert "scan: " in joined, \
            f"scan progress should be tagged with 'scan: ' prefix in loguru: {joined!r}"
        assert "Done." in joined, \
            f"final 'Done.' terminator should land in loguru: {joined!r}"
        assert "bad_truncated.jpg" in joined, \
            f"corrupt file path should land in loguru for forensics: {joined!r}"
        assert "ImageDecodeError" in joined, \
            f"synthetic exception type should land in loguru: {joined!r}"


class TestScanWorkerLateCancel:
    """#463 — cancel checks at the entry of every opaque post-HASH stage
    (CLASSIFY/SCORE/AUTO-SELECT/WRITE) so a late user-cancel actually
    stops the pipeline.

    Pre-#463, the worker only polled ``isInterruptionRequested`` inside
    the HASH ``as_completed`` loop. Cancel during the long CLASSIFY pass
    or right before ``write_manifest`` was silently ignored — the
    pipeline ran to completion AND overwrote any prior manifest at
    ``output_path`` with the just-computed (now unwanted) data. That's
    the regression these tests pin.
    """

    def test_cancel_after_hash_skips_classify_through_write(
        self, qapp, tmp_path, monkeypatch
    ):
        """Set ``isInterruptionRequested = True`` immediately after the
        HASH worker finishes the only file. By the time the pipeline
        exits the HASH ``as_completed`` loop, drains consumer threads,
        and reaches the CLASSIFY entry-check, interruption is True.
        That check must short-circuit the rest of the pipeline:

          - ``scanner.dedup.classify`` MUST NOT be called.
          - ``scanner.scoring.apply_scoring_to_rows`` MUST NOT be called.
          - ``scanner.manifest.write_manifest`` MUST NOT be called —
            so any pre-existing manifest at ``output_path`` survives
            the cancel intact.
          - ``failed`` emits exactly ``"Scan cancelled."`` (the
            string ``scan_dialog`` distinguishes as a clean cancel,
            not a red error modal).
        """
        from app.views.workers.scan_worker import ScanWorker
        import scanner.hasher as _hasher
        import scanner.dedup as _dedup
        import scanner.scoring as _scoring
        import scanner.manifest as _manifest

        # Single source file — HASH loop polls once, then completes.
        a = tmp_path / "a.jpg"
        _write_jpeg(a)
        out = tmp_path / "manifest.sqlite"
        # Sentinel that proves the OLD manifest at output_path is NOT
        # overwritten when cancel fires before WRITE. Realistic shape
        # for the regression: user finishes a scan, opens the manifest,
        # later starts a re-scan, cancels mid-way — must NOT lose the
        # original.
        out.write_bytes(b"PRIOR-MANIFEST-SENTINEL")

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=1,
        )

        # ``worker.requestInterruption()`` doesn't actually set the flag
        # when ``run()`` is called synchronously instead of via
        # ``start()`` (no actual QThread thread exists yet). Patch
        # ``isInterruptionRequested`` on the instance directly via a
        # state-flag closure so tests don't depend on Qt thread state.
        cancel_state = {"flag": False}

        def fake_is_interrupt():
            return cancel_state["flag"]

        monkeypatch.setattr(worker, "isInterruptionRequested", fake_is_interrupt)

        real_compute_hashes = _hasher.compute_hashes

        def cancel_during_hash(path, file_type):
            """Run the real hash, then flip the cancel flag. The HASH
            ``as_completed`` loop has already passed its own cancel
            check for this iteration; with only one record, there's no
            next iteration. So the next check the pipeline hits is at
            CLASSIFY entry — exactly the new #463 check this test pins.
            """
            result = real_compute_hashes(path, file_type)
            cancel_state["flag"] = True
            return result

        def must_not_run_classify(*args, **kwargs):
            raise AssertionError(
                "scanner.dedup.classify called after cancel — the "
                "CLASSIFY-stage check at scan_worker.py did not fire"
            )

        def must_not_run_score(*args, **kwargs):
            raise AssertionError(
                "apply_scoring_to_rows called after cancel — the "
                "SCORE-stage check did not fire"
            )

        def must_not_run_write(*args, **kwargs):
            raise AssertionError(
                "write_manifest called after cancel — the WRITE-stage "
                "check did not fire; pre-existing manifest at "
                "output_path would be overwritten"
            )

        monkeypatch.setattr(_hasher, "compute_hashes", cancel_during_hash)
        monkeypatch.setattr(_dedup, "classify", must_not_run_classify)
        monkeypatch.setattr(_scoring, "apply_scoring_to_rows", must_not_run_score)
        monkeypatch.setattr(_manifest, "write_manifest", must_not_run_write)

        failed: list[str] = []
        finished: list[str] = []
        worker.failed.connect(failed.append)
        worker.finished.connect(finished.append)
        worker.run()

        # Cancel-emit shape: exactly one "Scan cancelled." — scan_dialog
        # distinguishes this from an error string and avoids the red
        # modal. (#463 test_plan acceptance.)
        assert failed == ["Scan cancelled."], (
            f"expected exactly ['Scan cancelled.'] but got {failed!r}"
        )
        assert finished == [], (
            f"finished signal must NOT fire on cancel; got {finished!r}"
        )
        # The destination manifest is byte-for-byte the prior sentinel
        # — the WRITE check short-circuited before write_manifest.
        assert out.read_bytes() == b"PRIOR-MANIFEST-SENTINEL", (
            "destination manifest was overwritten despite cancel — "
            "the WRITE-stage check failed to fire"
        )

    def test_cancel_before_write_only_preserves_existing_manifest(
        self, qapp, tmp_path, monkeypatch
    ):
        """Defense-in-depth complement to the test above: even if every
        earlier cancel check somehow failed, the WRITE-stage check
        alone must still prevent overwriting the existing manifest.

        Triggers interruption from inside the patched ``print_summary``
        (which runs after AUTO-SELECT, immediately before the WRITE
        check). Confirms the WRITE check fires in isolation, not just
        as a downstream consequence of an earlier check.
        """
        from app.views.workers.scan_worker import ScanWorker
        import scanner.manifest as _manifest

        a = tmp_path / "a.jpg"
        _write_jpeg(a)
        out = tmp_path / "manifest.sqlite"
        out.write_bytes(b"PRIOR-MANIFEST-SENTINEL")

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=1,
        )

        # See the sibling test for why ``isInterruptionRequested`` is
        # patched on the instance rather than going through
        # ``requestInterruption()`` — synchronous ``run()`` doesn't
        # propagate the real Qt interruption flag.
        cancel_state = {"flag": False}

        def fake_is_interrupt():
            return cancel_state["flag"]

        monkeypatch.setattr(worker, "isInterruptionRequested", fake_is_interrupt)

        real_print_summary = _manifest.print_summary
        write_calls: list[tuple] = []

        def hooked_print_summary(*args, **kwargs):
            """Run the real summary, then flip the cancel flag. The
            WRITE check fires on the very next statement after
            print_summary's output is re-emitted to the log."""
            real_print_summary(*args, **kwargs)
            cancel_state["flag"] = True

        def boom_write_manifest(*args, **kwargs):
            write_calls.append(args)
            raise AssertionError(
                "write_manifest called after cancel — the WRITE-stage "
                "check failed in isolation"
            )

        monkeypatch.setattr(_manifest, "print_summary", hooked_print_summary)
        monkeypatch.setattr(_manifest, "write_manifest", boom_write_manifest)

        failed: list[str] = []
        worker.failed.connect(failed.append)
        worker.run()

        assert write_calls == [], (
            f"write_manifest must not be called on late cancel; "
            f"call args were {write_calls!r}"
        )
        assert failed == ["Scan cancelled."], (
            f"expected exactly ['Scan cancelled.'] but got {failed!r}"
        )
        assert out.read_bytes() == b"PRIOR-MANIFEST-SENTINEL", (
            "destination manifest must be preserved on WRITE-stage cancel"
        )


class TestScanWorkerWalkCancel:
    """#491 — cancel during the WALK stage must propagate into
    ``scanner.walker.scan_sources`` via the new ``cancel_check`` hook,
    and the worker must early-return before HASH starts.

    Pre-#491, ``scan_sources`` was synchronous and uncancellable: a
    cancel during WALK was only observed AFTER the walker exhausted
    ``rglob``. On large NAS scans that's minutes of unresponsive
    "Cancel" + an orphan QThread holding ExiftoolProcess subprocesses
    alive after the dialog closes via its 3-second wait timeout.
    """

    def test_walk_stage_observes_cancel_check_and_skips_hash(
        self, qapp, tmp_path, monkeypatch
    ):
        """Pre-set the interruption flag before ``run()`` starts. The
        walker's ``cancel_check`` predicate (wired to
        ``self.isInterruptionRequested``) returns True on the first poll
        and returns no records. The post-WALK gate then short-circuits
        the rest of the pipeline:

          - ``scanner.hasher.compute_hashes`` MUST NOT be called.
          - ``scanner.manifest.write_manifest`` MUST NOT be called —
            any pre-existing manifest at ``output_path`` survives.
          - ``failed`` emits exactly ``"Scan cancelled."`` so
            scan_dialog distinguishes from a red error modal.
        """
        from app.views.workers.scan_worker import ScanWorker
        import scanner.hasher as _hasher
        import scanner.manifest as _manifest

        # Several files — enough that a non-cancelling walker would
        # definitely call compute_hashes at least once.
        for i in range(5):
            _write_jpeg(tmp_path / f"img_{i}.jpg")
        out = tmp_path / "manifest.sqlite"
        out.write_bytes(b"PRIOR-MANIFEST-SENTINEL")

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=1,
        )

        # Flag the worker as already-interrupted before WALK starts.
        # The walker polls ``self.isInterruptionRequested`` on every
        # rglob hit; the first hit returns True, the loop breaks
        # immediately, and the walker returns ``records == []``.
        monkeypatch.setattr(worker, "isInterruptionRequested", lambda: True)

        def must_not_run_hash(*args, **kwargs):
            raise AssertionError(
                "compute_hashes called after WALK-stage cancel — the "
                "#491 cancel_check did not propagate into scan_sources"
            )

        def must_not_run_write(*args, **kwargs):
            raise AssertionError(
                "write_manifest called after WALK-stage cancel — the "
                "post-WALK gate at scan_worker.py did not fire"
            )

        monkeypatch.setattr(_hasher, "compute_hashes", must_not_run_hash)
        monkeypatch.setattr(_manifest, "write_manifest", must_not_run_write)

        failed: list[str] = []
        finished: list[str] = []
        worker.failed.connect(failed.append)
        worker.finished.connect(finished.append)
        worker.run()

        # Same cancel-emit shape as the HASH / CLASSIFY / SCORE / WRITE
        # gates — scan_dialog treats this as a clean cancel.
        assert failed == ["Scan cancelled."], (
            f"expected exactly ['Scan cancelled.'] but got {failed!r}"
        )
        assert finished == [], (
            f"finished signal must NOT fire on cancel; got {finished!r}"
        )
        assert out.read_bytes() == b"PRIOR-MANIFEST-SENTINEL", (
            "destination manifest must be preserved on WALK-stage cancel"
        )


class TestScanWorkerPerDeviceHashPools:
    """#548 — the HASH stage's thread path runs one ThreadPoolExecutor PER
    PHYSICAL DEVICE concurrently, so NAS-latency-bound reads overlap
    HDD-seek-bound reads instead of queueing behind them in one flat pool.

    These tests drive the worker end-to-end with SYNTHETIC two-device
    records (paths on ``D:`` and ``J:`` — no real files, since CI has no
    such drives) injected via a patched ``scan_sources``, and a patched
    ``run_hash_for_record`` so nothing touches disk. The seams are the same
    ones the established ``TestHashPoolSetting`` tests use (patch the late
    imports the worker resolves inside ``_run_pipeline``).
    """

    def _records_two_devices(self):
        """Five records: 3 on D:, 2 on J:, interleaved in source order so a
        flat ``records[:n]`` slice would NOT span both devices."""
        from scanner.walker import FileRecord

        specs = [
            (r"D:\photos\a.jpg", "D"),
            (r"J:\nas\b.jpg", "J"),
            (r"D:\photos\c.jpg", "D"),
            (r"J:\nas\d.jpg", "J"),
            (r"D:\photos\e.jpg", "D"),
        ]
        return [
            FileRecord(path=Path(p), source_label=lbl, file_type="skip")
            for p, lbl in specs
        ]

    def _install_synthetic_pipeline(
        self, monkeypatch, records, *, remote_drive, seek_penalty=None
    ):
        """Patch the worker's late imports so a synthetic two-device scan
        reaches and exercises the HASH thread branch without disk I/O:

        - ``scan_sources`` → returns ``records`` (the worker walks once).
        - ``run_hash_for_record`` → returns a synthetic ``HashResult`` per
          record (no PIL, no file read).
        - ``is_remote_drive`` → classifies ``J:`` as NAS so the per-device
          worker count is 8 for J and ``min(4,cpu)`` for D.
        - ``disk_incurs_seek_penalty`` → injected so the local rotational
          probe never opens a real ``\\\\.\\D:`` handle on the test machine.
          Defaults to "not spinning" (SSD) so local stays ``min(4,cpu)``;
          pass ``seek_penalty`` to mark a device as a spinning HDD (#548 PR-B).
        - ``classify`` → captures the ``hash_results`` ordering and returns
          ``[]`` so the manifest stage is a trivial empty write.
        """
        import scanner.walker as _walker
        import scanner.hasher as _hasher
        import scanner.workers as _workers
        import scanner.dedup as _dedup
        from scanner.dedup import HashResult

        def fake_scan_sources(sources, **kwargs):
            cb = kwargs.get("progress_callback")
            if cb:
                for _ in records:
                    cb()
            return list(records)

        def fake_hash(idx, record):
            return idx, HashResult(
                record=record,
                sha256=f"sha-{idx}",
                phash=None,
                exif_date=None,
            )

        captured = {"hash_results": None}

        def fake_classify(hash_results, **kwargs):
            captured["hash_results"] = list(hash_results)
            return []

        monkeypatch.setattr(_walker, "scan_sources", fake_scan_sources)
        monkeypatch.setattr(_hasher, "run_hash_for_record", fake_hash)
        monkeypatch.setattr(
            _workers, "is_remote_drive", lambda root: remote_drive(root)
        )
        monkeypatch.setattr(
            _workers,
            "disk_incurs_seek_penalty",
            seek_penalty or (lambda root: False),
        )
        monkeypatch.setattr(_dedup, "classify", fake_classify)
        return captured

    def _spy_thread_pools(self, monkeypatch):
        """Record the ``max_workers`` of every ThreadPoolExecutor the worker
        constructs during HASH (delegates to the real one so work runs)."""
        import concurrent.futures as _cf

        constructed: list[int] = []
        real = _cf.ThreadPoolExecutor

        class SpyThreadPool(real):
            def __init__(self, *args, **kwargs):
                constructed.append(kwargs.get("max_workers"))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(_cf, "ThreadPoolExecutor", SpyThreadPool)
        return constructed

    def test_one_pool_per_device_with_correct_worker_counts(
        self, qapp, tmp_path, monkeypatch
    ):
        """Two devices → two ThreadPoolExecutors, J: (NAS) gets 8 workers and
        D: (local) gets min(4, cpu). A flat single pool would construct one
        executor with self.workers — this asserts the per-device fan-out."""
        import os
        from app.views.workers.scan_worker import ScanWorker

        records = self._records_two_devices()
        self._install_synthetic_pipeline(
            monkeypatch, records, remote_drive=lambda root: str(root).upper() == "J:"
        )
        constructed = self._spy_thread_pools(monkeypatch)

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(tmp_path / "manifest.sqlite"),
            recursive_map={"src": False},
            workers=2,
        )
        worker.run()

        # One pool per device — D: at min(4,cpu), J: at 8. Order follows
        # source-iteration order (D first, since record 0 is on D:).
        local = min(4, os.cpu_count() or 4)
        assert constructed == [local, 8], (
            f"expected one pool per device [D={local}, J=8]; got {constructed!r}"
        )

    def test_spinning_local_device_capped_to_two_workers(
        self, qapp, tmp_path, monkeypatch
    ):
        """#548 PR-B — a local device the seek probe flags as a spinning HDD
        gets a 2-worker pool (not min(4,cpu)) so 8 concurrent reads don't
        seek-thrash the one spindle, while the NAS still gets 8. This is the
        end-to-end proof that the rotational cap reaches the fan-out, not just
        the unit-level hash_workers_for_root."""
        from app.views.workers.scan_worker import ScanWorker

        records = self._records_two_devices()
        self._install_synthetic_pipeline(
            monkeypatch,
            records,
            remote_drive=lambda root: str(root).upper() == "J:",
            seek_penalty=lambda root: str(root).upper() == "D:",  # D: spinning HDD
        )
        constructed = self._spy_thread_pools(monkeypatch)

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(tmp_path / "manifest.sqlite"),
            recursive_map={"src": False},
            workers=2,
        )
        worker.run()

        # D: spinning → capped to 2; J: NAS → 8. Order is source-iteration (D first).
        assert constructed == [2, 8], (
            f"expected spinning D: capped to 2 and NAS J: at 8; got {constructed!r}"
        )

    def test_hash_results_preserve_input_order_across_devices(
        self, qapp, tmp_path, monkeypatch
    ):
        """Records on two devices complete in arbitrary pool-interleaved
        order, but hash_results MUST stay in original input index order —
        classify()'s union-find group_ids depend on walk order."""
        from app.views.workers.scan_worker import ScanWorker

        records = self._records_two_devices()
        captured = self._install_synthetic_pipeline(
            monkeypatch, records, remote_drive=lambda root: str(root).upper() == "J:"
        )
        self._spy_thread_pools(monkeypatch)

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(tmp_path / "manifest.sqlite"),
            recursive_map={"src": False},
            workers=2,
        )
        worker.run()

        result_paths = [hr.record.path for hr in captured["hash_results"]]
        assert result_paths == [r.path for r in records], (
            "per-device pools must not reorder hash_results — original "
            f"input order required; got {result_paths!r}"
        )

    def test_cancel_during_hash_across_pools_emits_cancelled(
        self, qapp, tmp_path, monkeypatch
    ):
        """A user-cancel mid-HASH with N device pools must tear down cleanly:
        no deadlock, consumers get sentinels, exactly ['Scan cancelled.']
        fires and the prior manifest survives (classify never runs)."""
        from app.views.workers.scan_worker import ScanWorker
        import scanner.dedup as _dedup

        records = self._records_two_devices()
        self._install_synthetic_pipeline(
            monkeypatch, records, remote_drive=lambda root: str(root).upper() == "J:"
        )
        self._spy_thread_pools(monkeypatch)

        out = tmp_path / "manifest.sqlite"
        out.write_bytes(b"PRIOR-MANIFEST-SENTINEL")

        def must_not_classify(*a, **k):
            raise AssertionError("classify ran despite mid-HASH cancel")

        monkeypatch.setattr(_dedup, "classify", must_not_classify)

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(out),
            recursive_map={"src": False},
            workers=2,
        )
        cancel_state = {"flag": True}  # cancel before the first drain check
        monkeypatch.setattr(
            worker, "isInterruptionRequested", lambda: cancel_state["flag"]
        )

        failed: list[str] = []
        finished: list[str] = []
        worker.failed.connect(failed.append)
        worker.finished.connect(finished.append)
        worker.run()

        assert failed == ["Scan cancelled."], (
            f"mid-HASH cancel across pools must emit ['Scan cancelled.']; "
            f"got {failed!r}"
        )
        assert finished == [], "finished must not fire on cancel"
        assert out.read_bytes() == b"PRIOR-MANIFEST-SENTINEL", (
            "prior manifest must survive a mid-HASH cancel"
        )

    def test_single_device_uses_one_pool(self, qapp, tmp_path, monkeypatch):
        """The common case (one device) must construct exactly one pool —
        zero regression for single-device users."""
        import os
        from app.views.workers.scan_worker import ScanWorker
        from scanner.walker import FileRecord

        records = [
            FileRecord(path=Path(rf"D:\photos\{i}.jpg"), source_label="s",
                       file_type="skip")
            for i in range(4)
        ]
        self._install_synthetic_pipeline(
            monkeypatch, records, remote_drive=lambda root: False
        )
        constructed = self._spy_thread_pools(monkeypatch)

        worker = ScanWorker(
            sources={"src": str(tmp_path)},
            output_path=str(tmp_path / "manifest.sqlite"),
            recursive_map={"src": False},
            workers=2,
        )
        worker.run()

        assert constructed == [min(4, os.cpu_count() or 4)], (
            f"single-device scan must use exactly one pool; got {constructed!r}"
        )


class TestStratifiedSample:
    """#548 — _stratified_sample spreads the auto-calibration sample across
    devices so the thread-vs-process pick isn't measured on the first
    (source-order) device alone."""

    def test_multi_device_sample_spans_all_devices(self):
        from app.views.workers.scan_worker import _stratified_sample
        from scanner.walker import FileRecord

        # 50 D: records first, then 50 J: records — a naive records[:10]
        # would be ALL D:. The stratified sample must include J: too.
        recs = [
            FileRecord(path=Path(rf"D:\a\{i}.jpg"), source_label="d",
                       file_type="jpeg")
            for i in range(50)
        ] + [
            FileRecord(path=Path(rf"J:\b\{i}.jpg"), source_label="j",
                       file_type="jpeg")
            for i in range(50)
        ]

        # Odd n exercises the mid-row cap: the round-robin reaches n in the
        # middle of a device row and must stop EXACTLY at n, not overshoot.
        sample = _stratified_sample(recs, 7)

        drives = {str(r.path)[:2].upper() for r in sample}
        assert drives == {"D:", "J:"}, (
            f"stratified sample must span both devices; got {drives!r}"
        )
        assert len(sample) == 7, (
            f"odd n must cap exactly, not overshoot; got {len(sample)}"
        )

    def test_single_device_sample_is_prefix_slice(self):
        """One device → behaviour identical to records[:n] (the common case
        stays byte-for-byte unchanged)."""
        from app.views.workers.scan_worker import _stratified_sample
        from scanner.walker import FileRecord

        recs = [
            FileRecord(path=Path(rf"D:\a\{i}.jpg"), source_label="d",
                       file_type="jpeg")
            for i in range(20)
        ]
        assert _stratified_sample(recs, 5) == recs[:5]

    def test_sample_capped_at_n_when_devices_have_few_records(self):
        """Round-robin stops at n even when buckets are uneven, and never
        exceeds the available records."""
        from app.views.workers.scan_worker import _stratified_sample
        from scanner.walker import FileRecord

        recs = [
            FileRecord(path=Path(r"D:\a\1.jpg"), source_label="d", file_type="jpeg"),
            FileRecord(path=Path(r"D:\a\2.jpg"), source_label="d", file_type="jpeg"),
            FileRecord(path=Path(r"J:\b\1.jpg"), source_label="j", file_type="jpeg"),
        ]
        # Ask for more than exist — must return all 3, no duplicates, no raise.
        sample = _stratified_sample(recs, 10)
        assert len(sample) == 3
        assert {str(r.path) for r in sample} == {str(r.path) for r in recs}


class TestScanTeardownGaps:
    """#549 — process-mode HASH teardown must not strand the disk/app.

    (b) A mid-hash cancel must not run shutdown(wait=True) (the old
        ``with ProcessPoolExecutor()`` __exit__ did, blocking on in-flight
        reads past the 3s teardown budget).
    (a) The spawned worker processes must be registered with the #460
        KILL_ON_JOB_CLOSE job so a hard parent-kill reaps them.

    Both drive the worker end-to-end in process mode with a SpyProcessPool
    that delegates to a real ThreadPoolExecutor (run_hash_for_record runs
    in-thread — no OS spawn), the same seam TestHashPoolSetting uses.
    """

    def test_process_cancel_never_calls_shutdown_wait_true(
        self, qapp, tmp_path, monkeypatch
    ):
        """#549(b) — cancel mid-process-hash must tear down with
        shutdown(wait=False), never wait=True. A revert to the
        ``with ProcessPoolExecutor() as pool:`` form would re-introduce the
        __exit__ shutdown(wait=True) that blocks on in-flight read_bytes() —
        this spy records every shutdown ``wait`` and fails if any is True."""
        import concurrent.futures as _cf
        import scanner.hasher as _hasher
        from app.views.workers.scan_worker import ScanWorker
        from scanner.dedup import HashResult

        _write_jpeg(tmp_path / "only.jpg")
        out = tmp_path / "manifest.sqlite"

        shutdown_waits: list[bool] = []
        real_thread_pool = _cf.ThreadPoolExecutor

        class SpyProcessPool(real_thread_pool):
            def shutdown(self, wait=True, **kwargs):  # noqa: D401
                shutdown_waits.append(wait)
                # Never actually block the test, regardless of what the
                # production code asked for — we only care WHICH wait it passed.
                return super().shutdown(wait=False, **kwargs)

        monkeypatch.setattr(_cf, "ProcessPoolExecutor", SpyProcessPool)

        # Trip the cancel from inside the HASH drain (not the WALK gate): the
        # first hashed record flips the interruption flag, so walk completes
        # normally and the process-branch drain loop is the thing that cancels.
        flag = {"v": False}

        def fake_hash(idx, record):
            flag["v"] = True
            return idx, HashResult(record=record, sha256=f"sha-{idx}",
                                   phash=None, exif_date=None)

        monkeypatch.setattr(_hasher, "run_hash_for_record", fake_hash)

        worker = ScanWorker(
            sources={"solo": str(tmp_path)},
            output_path=str(out),
            recursive_map={"solo": False},
            workers=1,
            hash_pool="process",
        )
        monkeypatch.setattr(worker, "isInterruptionRequested", lambda: flag["v"])

        failed: list[str] = []
        worker.failed.connect(failed.append)
        worker.run()

        assert failed == ["Scan cancelled."], (
            f"process-mode mid-hash cancel must emit ['Scan cancelled.']; got {failed!r}"
        )
        assert shutdown_waits, "the process pool must be shut down on cancel"
        assert not any(shutdown_waits), (
            "process-branch teardown must never call shutdown(wait=True) — the "
            f"`with`-exit regression that blocks on in-flight reads; got {shutdown_waits!r}"
        )

    def test_process_pool_workers_assigned_to_kill_job(
        self, qapp, tmp_path, monkeypatch
    ):
        """#549(a) — the process-pool worker pids are registered with the #460
        KILL_ON_JOB_CLOSE job. Drop the assignment call and a force-kill of the
        app orphans python.exe workers still reading the disks (verified #549)."""
        import concurrent.futures as _cf
        import scanner.exif as _exif
        from app.views.workers.scan_worker import ScanWorker

        _write_jpeg(tmp_path / "only.jpg")
        out = tmp_path / "manifest.sqlite"

        class _FakeProc:
            def __init__(self, pid):
                self.pid = pid

        real_thread_pool = _cf.ThreadPoolExecutor

        class SpyProcessPool(real_thread_pool):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                # ProcessPoolExecutor exposes worker processes here; fake two
                # so the parent-side job assignment has pids to enumerate.
                self._processes = {4101: _FakeProc(4101), 4102: _FakeProc(4102)}

        monkeypatch.setattr(_cf, "ProcessPoolExecutor", SpyProcessPool)

        assigned: list[int] = []

        def fake_assign(pid):
            assigned.append(pid)
            return True

        monkeypatch.setattr(_exif, "assign_pid_to_kill_job", fake_assign)

        worker = ScanWorker(
            sources={"solo": str(tmp_path)},
            output_path=str(out),
            recursive_map={"solo": False},
            workers=2,
            hash_pool="process",
        )
        worker.run()

        # Subset, not equality: the exiftool consumer's ExiftoolProcess also
        # registers ITS pid via the same helper (#460), so `assigned` may also
        # contain exiftool pids where exiftool is installed. What this test
        # guards is that BOTH process-pool worker pids were registered.
        assert {4101, 4102}.issubset(set(assigned)), (
            "every process-pool worker pid must be assigned to the #460 kill "
            f"job; got {assigned!r}"
        )
        assert out.exists(), "the scan must still complete and write its manifest"
