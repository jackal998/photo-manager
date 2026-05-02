"""Tests for ManifestLoadWorker — background manifest loading via QThread.

Pattern: invoke ``worker.run()`` directly (same-thread, DirectConnection)
instead of ``worker.start()`` + ``worker.wait()``. coverage.py only tracks
the calling thread by default, so synchronous execution is what makes the
``_load()`` body actually count toward coverage. test_scan_worker.py uses
the same idiom for the same reason.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PIL import Image


_DDL = """
CREATE TABLE migration_manifest (
    id               INTEGER PRIMARY KEY,
    source_path      TEXT NOT NULL,
    source_label     TEXT NOT NULL,
    dest_path        TEXT,
    action           TEXT NOT NULL,
    source_hash      TEXT,
    phash            TEXT,
    hamming_distance INTEGER,
    group_id         TEXT,
    reason           TEXT,
    executed         INTEGER NOT NULL DEFAULT 0,
    user_decision    TEXT    NOT NULL DEFAULT '',
    file_size_bytes  INTEGER,
    shot_date        TEXT,
    creation_date    TEXT,
    mtime            TEXT,
    pixel_width      INTEGER,
    pixel_height     INTEGER
);
"""


def _make_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (128, 64, 32)).save(path, "JPEG")


def _seed_grouped_manifest(tmp_path: Path) -> Path:
    """Return a manifest with one near-duplicate pair (survives load filter)."""
    cand = tmp_path / "cand.jpg"
    ref = tmp_path / "ref.jpg"
    _make_jpeg(cand)
    _make_jpeg(ref)
    db = tmp_path / "manifest.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(_DDL)
        gid = "/group/a"
        conn.execute(
            "INSERT INTO migration_manifest (source_path, source_label, action, "
            "hamming_distance, group_id, reason) VALUES (?, ?, ?, ?, ?, ?)",
            (str(cand), "src", "REVIEW_DUPLICATE", 5, gid, "near-duplicate"),
        )
        conn.execute(
            "INSERT INTO migration_manifest (source_path, source_label, action, "
            "group_id, reason) VALUES (?, ?, ?, ?, ?)",
            (str(ref), "src", "MOVE", gid, "unique"),
        )
        conn.commit()
    return db


class TestManifestLoadWorkerSuccess:
    def test_emits_progress_then_finished_for_valid_manifest(self, qapp, tmp_path):
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        db = _seed_grouped_manifest(tmp_path)
        worker = ManifestLoadWorker(str(db), default_sort=[])

        progress: list[str] = []
        finished: list[list] = []
        failed: list[str] = []
        worker.progress.connect(progress.append)
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        worker.run()

        assert not failed, f"valid load must not emit failed; got: {failed}"
        assert len(finished) == 1, f"finished should fire once; got {len(finished)}"
        groups = finished[0]
        assert len(groups) == 1
        assert len(groups[0].items) == 2

        # Progress narrates the three pipeline stages.
        assert any("Loading manifest" in p for p in progress)
        assert any("Grouping" in p for p in progress)
        assert any("Loaded" in p and "group" in p for p in progress)

    def test_default_sort_branch_executes_when_provided(self, qapp, tmp_path):
        """default_sort=[(field, asc)] triggers the SortService.sort branch."""
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        db = _seed_grouped_manifest(tmp_path)
        worker = ManifestLoadWorker(
            str(db), default_sort=[("file_size_bytes", False)]
        )
        finished: list[list] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(lambda _: None)

        worker.run()

        assert finished
        assert len(finished[0]) == 1

    def test_empty_manifest_yields_zero_groups(self, qapp, tmp_path):
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        db = tmp_path / "empty.sqlite"
        with sqlite3.connect(db) as conn:
            conn.executescript(_DDL)
            conn.commit()

        worker = ManifestLoadWorker(str(db), default_sort=[])
        finished: list[list] = []
        worker.finished.connect(finished.append)
        worker.failed.connect(lambda _: None)

        worker.run()

        assert finished == [[]], f"empty manifest should finish with []; got {finished!r}"


class TestManifestLoadWorkerFailure:
    def test_missing_manifest_emits_failed_with_message(self, qapp, tmp_path):
        """Non-existent path → ManifestRepository.load raises → run() catches → failed.

        Exercises the run() exception handler at line 50-52, which the
        original test file's threaded approach didn't reach in the
        coverage tracker (coverage.py is per-thread by default).
        """
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        worker = ManifestLoadWorker(
            str(tmp_path / "does_not_exist.sqlite"), default_sort=[]
        )
        progress: list[str] = []
        finished: list[list] = []
        failed: list[str] = []
        worker.progress.connect(progress.append)
        worker.finished.connect(finished.append)
        worker.failed.connect(failed.append)

        worker.run()

        assert not finished, "no finished signal expected on failure"
        assert len(failed) == 1, f"failed should fire once; got {failed!r}"
        assert "Manifest not found" in failed[0] or "does_not_exist" in failed[0]
