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
