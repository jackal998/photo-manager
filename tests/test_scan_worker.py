"""Tests for app/views/workers/scan_worker.py — ScanWorker pipeline behaviour.

Focus: regression test for issue #46 — one bad file must never abort the whole
scan. Verifies that a per-file ``compute_hashes`` exception is logged and
skipped, and the pipeline still produces a manifest.
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

        out = tmp_path / "manifest.csv"
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
