"""Tests for infrastructure/manifest_repository.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from infrastructure.manifest_repository import ManifestRepository
from core.models import PhotoGroup, PhotoRecord


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
    duplicate_of     TEXT,
    reason           TEXT,
    executed         INTEGER NOT NULL DEFAULT 0
);
"""


def _make_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    db = tmp_path / "manifest.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(_DDL)
        for r in rows:
            conn.execute(
                "INSERT INTO migration_manifest "
                "(source_path, source_label, dest_path, action, hamming_distance, "
                " duplicate_of, reason, executed) "
                "VALUES (:source_path, :source_label, :dest_path, :action, "
                "        :hamming_distance, :duplicate_of, :reason, :executed)",
                r,
            )
        conn.commit()
    return db


def _row(overrides: dict) -> dict:
    base = {
        "source_path": "/jdrive/a.jpg",
        "source_label": "jdrive",
        "dest_path": None,
        "action": "REVIEW_DUPLICATE",
        "hamming_distance": 5,
        "duplicate_of": "/takeout/a.jpg",
        "reason": "near-duplicate (hamming=5)",
        "executed": 0,
    }
    return {**base, **overrides}


def _ref_row(overrides: dict = {}) -> dict:
    base = {
        "source_path": "/takeout/a.jpg",
        "source_label": "takeout",
        "dest_path": "2024/20240601_takeout/a.jpg",
        "action": "MOVE",
        "hamming_distance": None,
        "duplicate_of": None,
        "reason": "unique",
        "executed": 0,
    }
    return {**base, **overrides}


def _make_jpeg(path: Path) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (128, 64, 32)).save(path, "JPEG")


class TestManifestRepositoryLoad:
    def test_raises_on_missing_manifest(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            list(ManifestRepository().load(str(tmp_path / "missing.sqlite")))

    def test_returns_two_records_per_pair(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 2

    def test_candidate_is_pre_marked(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].is_mark is True
        assert records[str(cand)].is_locked is False

    def test_reference_is_locked_not_marked(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(ref)].is_locked is True
        assert records[str(ref)].is_mark is False

    def test_same_group_number_for_pair(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = list(ManifestRepository().load(str(db)))
        group_numbers = {r.group_number for r in records}
        assert len(group_numbers) == 1  # both in same group

    def test_skips_row_with_missing_source_file(self, tmp_path):
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(ref)

        db = _make_manifest(tmp_path, [
            _row({"source_path": str(tmp_path / "missing.jpg"), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        # Should not raise; missing candidate is skipped gracefully
        records = list(ManifestRepository().load(str(db)))
        assert all(r.file_path != str(tmp_path / "missing.jpg") for r in records)

    def test_empty_when_no_review_duplicate_rows(self, tmp_path):
        db = _make_manifest(tmp_path, [
            {**_row({}), "action": "MOVE"},
        ])
        assert list(ManifestRepository().load(str(db))) == []


class TestManifestRepositorySave:
    def _make_group(self, cand_path: str, ref_path: str, cand_mark: bool) -> PhotoGroup:
        cand = PhotoRecord(
            group_number=1, is_mark=cand_mark, is_locked=False,
            folder_path="", file_path=cand_path,
            capture_date=None, modified_date=None, file_size_bytes=0,
        )
        ref = PhotoRecord(
            group_number=1, is_mark=False, is_locked=True,
            folder_path="", file_path=ref_path,
            capture_date=None, modified_date=None, file_size_bytes=0,
        )
        return PhotoGroup(group_number=1, items=[cand, ref])

    def test_marked_candidate_becomes_skip(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/jdrive/a.jpg", "duplicate_of": "/takeout/a.jpg"}),
        ])
        group = self._make_group("/jdrive/a.jpg", "/takeout/a.jpg", cand_mark=True)
        ManifestRepository().save(str(db), [group])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT action, executed FROM migration_manifest WHERE source_path = '/jdrive/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "SKIP"
        assert row[1] == 1

    def test_unmarked_candidate_becomes_move(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/jdrive/a.jpg", "duplicate_of": "/takeout/a.jpg"}),
        ])
        group = self._make_group("/jdrive/a.jpg", "/takeout/a.jpg", cand_mark=False)
        ManifestRepository().save(str(db), [group])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT action FROM migration_manifest WHERE source_path = '/jdrive/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "MOVE"

    def test_locked_reference_not_updated(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/jdrive/a.jpg", "duplicate_of": "/takeout/a.jpg"}),
            _ref_row({"source_path": "/takeout/a.jpg"}),
        ])
        group = self._make_group("/jdrive/a.jpg", "/takeout/a.jpg", cand_mark=True)
        ManifestRepository().save(str(db), [group])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT action FROM migration_manifest WHERE source_path = '/takeout/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "MOVE"  # unchanged
