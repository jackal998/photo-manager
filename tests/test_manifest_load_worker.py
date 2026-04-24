"""Tests for ManifestLoadWorker — background manifest loading via QThread."""

from __future__ import annotations

from pathlib import Path

import pytest

from scanner.dedup import ManifestRow
from scanner.manifest import write_manifest


def _make_row(path: str) -> ManifestRow:
    return ManifestRow(
        source_path=path,
        source_label="jdrive",
        dest_path=None,
        action="MOVE",
        source_hash="abc123",
        phash=None,
        hamming_distance=None,
        duplicate_of=None,
        reason="unique",
    )


class TestManifestLoadWorker:
    def _run_worker(self, qapp, worker, timeout_ms: int = 10_000) -> None:
        """Start worker, wait for it, then flush any queued cross-thread signals."""
        worker.start()
        assert worker.wait(timeout_ms), "Worker did not finish within timeout"
        qapp.processEvents()  # deliver queued signals from worker thread → main thread

    def test_worker_emits_finished_with_groups(self, qapp, tmp_path):
        """Worker yields PhotoGroups via finished signal for a valid manifest."""
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        db = tmp_path / "manifest.sqlite"
        write_manifest([_make_row(str(f))], db)

        finished_groups: list = []
        worker = ManifestLoadWorker(str(db), [], parent=None)
        worker.finished.connect(lambda groups: finished_groups.extend(groups))
        self._run_worker(qapp, worker)

        assert len(finished_groups) == 1
        assert finished_groups[0].items[0].file_path == str(f)

    def test_worker_emits_failed_on_bad_path(self, qapp):
        """Worker emits failed signal when the manifest path does not exist."""
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        errors: list[str] = []
        worker = ManifestLoadWorker("/nonexistent/manifest.sqlite", [], parent=None)
        worker.failed.connect(errors.append)
        self._run_worker(qapp, worker)

        assert len(errors) == 1

    def test_worker_emits_progress_strings(self, qapp, tmp_path):
        """Worker emits at least one progress message before finishing."""
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        db = tmp_path / "empty.sqlite"
        write_manifest([], db)

        messages: list[str] = []
        worker = ManifestLoadWorker(str(db), [], parent=None)
        worker.progress.connect(messages.append)
        self._run_worker(qapp, worker)

        assert len(messages) >= 1

    def test_worker_empty_manifest_yields_no_groups(self, qapp, tmp_path):
        """Empty manifest produces an empty groups list (no crash)."""
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        db = tmp_path / "empty.sqlite"
        write_manifest([], db)

        finished_groups: list = []
        worker = ManifestLoadWorker(str(db), [], parent=None)
        worker.finished.connect(lambda groups: finished_groups.extend(groups))
        self._run_worker(qapp, worker)

        assert finished_groups == []
