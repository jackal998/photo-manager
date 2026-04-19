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
    executed         INTEGER NOT NULL DEFAULT 0,
    user_decision    TEXT    NOT NULL DEFAULT ''
);
"""

_DDL_NO_USER_DECISION = """
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


def _make_manifest(tmp_path: Path, rows: list[dict], ddl: str = _DDL) -> Path:
    db = tmp_path / "manifest.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(ddl)
        for r in rows:
            cols = list(r.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            col_list = ", ".join(cols)
            conn.execute(
                f"INSERT INTO migration_manifest ({col_list}) VALUES ({placeholders})",
                r,
            )
        conn.commit()
    return db


def _row(overrides: dict) -> dict:
    base = {
        "source_path": "/source/a.jpg",
        "source_label": "jdrive",
        "dest_path": None,
        "action": "REVIEW_DUPLICATE",
        "hamming_distance": 5,
        "duplicate_of": "/reference/a.jpg",
        "reason": "near-duplicate (hamming=5)",
        "executed": 0,
        "user_decision": "",
    }
    return {**base, **overrides}


def _ref_row(overrides: dict = {}) -> dict:
    base = {
        "source_path": "/reference/a.jpg",
        "source_label": "takeout",
        "dest_path": "2024/20240601_takeout/a.jpg",
        "action": "MOVE",
        "hamming_distance": None,
        "duplicate_of": None,
        "reason": "unique",
        "executed": 0,
        "user_decision": "",
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

    def test_candidate_not_pre_marked(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].is_mark is False
        assert records[str(cand)].is_locked is False

    def test_reference_not_locked(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(ref)].is_locked is False
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

    def test_move_row_yields_single_record(self, tmp_path):
        f = tmp_path / "photo.jpg"
        _make_jpeg(f)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f), "action": "MOVE", "duplicate_of": None, "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 1
        assert records[0].action == "MOVE"
        assert records[0].is_mark is False
        assert records[0].is_locked is False

    def test_keep_row_not_locked(self, tmp_path):
        f = tmp_path / "keep.jpg"
        _make_jpeg(f)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f), "action": "KEEP", "duplicate_of": None, "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 1
        assert records[0].is_locked is False
        assert records[0].is_mark is False

    def test_undated_row_yields_single_record(self, tmp_path):
        f = tmp_path / "undated.jpg"
        _make_jpeg(f)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f), "action": "UNDATED", "duplicate_of": None, "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 1
        assert records[0].action == "UNDATED"
        assert records[0].is_mark is False
        assert records[0].is_locked is False

    def test_exact_row_yields_pair(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "EXACT", "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 2
        by_path = {r.file_path: r for r in records}
        assert by_path[str(cand)].is_mark is False
        assert by_path[str(ref)].is_locked is False

    def test_ref_not_duplicated_as_standalone(self, tmp_path):
        """A file appearing as duplicate_of in a SKIP pair must not also appear as a standalone row."""
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "SKIP", "duplicate_of": str(ref)}),
            # ref has its own MOVE row — should NOT appear twice
            _ref_row({"source_path": str(ref)}),
        ])
        records = list(ManifestRepository().load(str(db)))
        ref_records = [r for r in records if r.file_path == str(ref)]
        assert len(ref_records) == 1  # inline reference only, not also standalone

    def test_action_field_set_on_record(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].action == "REVIEW_DUPLICATE"
        assert records[str(ref)].action == ""  # reference role — no action

    def test_user_decision_defaults_to_empty_string(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "duplicate_of": str(ref)}),
            _ref_row({"source_path": str(ref)}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].user_decision == ""
        assert records[str(ref)].user_decision == ""

    def test_user_decision_preserved_on_load(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        _make_jpeg(cand)
        db = _make_manifest(tmp_path, [
            _row({
                "source_path": str(cand),
                "action": "MOVE",
                "duplicate_of": None,
                "hamming_distance": None,
                "user_decision": "delete",
            }),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert records[0].user_decision == "delete"

    def test_user_decision_missing_column_migrated(self, tmp_path):
        """Older DBs without user_decision column are migrated automatically."""
        cand = tmp_path / "jdrive" / "a.jpg"
        _make_jpeg(cand)
        db = _make_manifest(tmp_path, [
            {
                "source_path": str(cand),
                "source_label": "jdrive",
                "dest_path": None,
                "action": "MOVE",
                "hamming_distance": None,
                "duplicate_of": None,
                "reason": "unique",
                "executed": 0,
            },
        ], ddl=_DDL_NO_USER_DECISION)
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 1
        assert records[0].user_decision == ""


class TestManifestRepositorySave:
    def _make_record(
        self, path: str, action: str, user_decision: str = "", locked: bool = False
    ) -> PhotoRecord:
        return PhotoRecord(
            group_number=1, is_mark=False, is_locked=locked,
            folder_path="", file_path=path,
            capture_date=None, modified_date=None, file_size_bytes=0,
            action=action,
            user_decision=user_decision,
        )

    def _make_group(
        self,
        cand_path: str,
        cand_action: str,
        ref_path: str,
        ref_action: str = "",
        cand_decision: str = "",
        ref_decision: str = "",
    ) -> PhotoGroup:
        return PhotoGroup(group_number=1, items=[
            self._make_record(cand_path, cand_action, cand_decision),
            self._make_record(ref_path, ref_action, ref_decision),
        ])

    def test_user_decision_written_to_db(self, tmp_path):
        """save() writes rec.user_decision to the DB."""
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "duplicate_of": "/reference/a.jpg"}),
        ])
        group = PhotoGroup(group_number=1, items=[
            self._make_record("/source/a.jpg", "REVIEW_DUPLICATE", "delete"),
        ])
        ManifestRepository().save(str(db), [group])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/source/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "delete"

    def test_user_decision_keep_written(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "action": "MOVE",
                  "duplicate_of": None, "hamming_distance": None}),
        ])
        group = PhotoGroup(group_number=1, items=[
            self._make_record("/source/a.jpg", "MOVE", "keep"),
        ])
        ManifestRepository().save(str(db), [group])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/source/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "keep"

    def test_user_decision_empty_written(self, tmp_path):
        """save() writes empty string when user_decision is unset."""
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "user_decision": "delete",
                  "duplicate_of": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        group = PhotoGroup(group_number=1, items=[
            self._make_record("/source/a.jpg", "MOVE", ""),
        ])
        ManifestRepository().save(str(db), [group])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/source/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == ""

    def test_scanner_action_unchanged_by_save(self, tmp_path):
        """save() must not modify the scanner's action column."""
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "duplicate_of": "/reference/a.jpg"}),
        ])
        group = PhotoGroup(group_number=1, items=[
            self._make_record("/source/a.jpg", "REVIEW_DUPLICATE", "delete"),
        ])
        ManifestRepository().save(str(db), [group])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT action FROM migration_manifest WHERE source_path = '/source/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "REVIEW_DUPLICATE"  # scanner classification untouched

    def test_save_returns_row_count(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "duplicate_of": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/source/b.jpg", "duplicate_of": None,
                  "hamming_distance": None, "action": "MOVE"}),
        ])
        group = PhotoGroup(group_number=1, items=[
            self._make_record("/source/a.jpg", "MOVE", "keep"),
            self._make_record("/source/b.jpg", "MOVE", "delete"),
        ])
        count = ManifestRepository().save(str(db), [group])
        assert count == 2


class TestManifestRepositoryUpdateDecision:
    def test_update_decision_sets_single_row(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "duplicate_of": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/source/b.jpg", "duplicate_of": None,
                  "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().update_decision(str(db), "/source/a.jpg", "delete")

        conn = sqlite3.connect(db)
        rows = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT source_path, user_decision FROM migration_manifest"
            ).fetchall()
        }
        conn.close()
        assert rows["/source/a.jpg"] == "delete"
        assert rows["/source/b.jpg"] == ""  # untouched

    def test_update_decision_overwrites_existing(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "user_decision": "delete",
                  "duplicate_of": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().update_decision(str(db), "/source/a.jpg", "keep")

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/source/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "keep"
