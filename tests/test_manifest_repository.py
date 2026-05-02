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
    group_id         TEXT,
    reason           TEXT,
    executed         INTEGER NOT NULL DEFAULT 0,
    user_decision    TEXT    NOT NULL DEFAULT ''
);
"""

_DDL_WITH_METADATA = """
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
    mtime            TEXT
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
    group_id         TEXT,
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
    """Create a REVIEW_DUPLICATE row defaulting to group_id='/group/a'."""
    base = {
        "source_path": "/source/a.jpg",
        "source_label": "jdrive",
        "dest_path": None,
        "action": "REVIEW_DUPLICATE",
        "hamming_distance": 5,
        "group_id": "/group/a",
        "reason": "near-duplicate (hamming=5)",
        "executed": 0,
        "user_decision": "",
    }
    return {**base, **overrides}


def _ref_row(overrides: dict = {}) -> dict:
    """Create a companion MOVE row that shares the same default group_id."""
    base = {
        "source_path": "/reference/a.jpg",
        "source_label": "takeout",
        "dest_path": "2024/20240601_takeout/a.jpg",
        "action": "MOVE",
        "hamming_distance": None,
        "group_id": "/group/a",
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

        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 2

    def test_candidate_not_pre_marked(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].is_mark is False
        assert records[str(cand)].is_locked is False

    def test_reference_not_locked(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(ref)].is_locked is False
        assert records[str(ref)].is_mark is False

    def test_same_group_number_for_pair(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)

        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = list(ManifestRepository().load(str(db)))
        group_numbers = {r.group_number for r in records}
        assert len(group_numbers) == 1  # both in same group

    def test_yields_row_for_missing_source_file(self, tmp_path):
        """load() yields rows even when the source file does not exist on disk.

        The existence check was moved to execute time (ExecuteActionDialog._delete_file)
        so that opening a manifest on a NAS doesn't require 40 K stat() round-trips.
        Missing files are reported to the user only when they click Execute.
        """
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(ref)

        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(tmp_path / "missing.jpg"), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        # Missing candidate is now yielded — no existence check at load time
        records = list(ManifestRepository().load(str(db)))
        paths = {r.file_path for r in records}
        assert str(tmp_path / "missing.jpg") in paths

    def test_singleton_move_not_yielded(self, tmp_path):
        """MOVE rows with no group_id are singletons — not shown in the review UI."""
        f = tmp_path / "photo.jpg"
        _make_jpeg(f)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f), "action": "MOVE", "group_id": None, "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 0

    def test_singleton_keep_not_yielded(self, tmp_path):
        """KEEP rows with no group_id are not shown in the review UI."""
        f = tmp_path / "keep.jpg"
        _make_jpeg(f)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f), "action": "KEEP", "group_id": None, "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 0

    def test_singleton_undated_not_yielded(self, tmp_path):
        """UNDATED rows with no group_id are not shown in the review UI."""
        f = tmp_path / "undated.jpg"
        _make_jpeg(f)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f), "action": "UNDATED", "group_id": None, "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 0

    def test_exact_row_yields_pair(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "EXACT", "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 2
        by_path = {r.file_path: r for r in records}
        assert by_path[str(cand)].is_mark is False
        assert by_path[str(ref)].is_locked is False

    def test_each_group_member_appears_exactly_once(self, tmp_path):
        """Each file in a group must be yielded exactly once — no duplicates."""
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = list(ManifestRepository().load(str(db)))
        paths = [r.file_path for r in records]
        assert len(paths) == len(set(paths))  # no duplicates

    def test_action_field_set_on_record(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].action == "REVIEW_DUPLICATE"
        assert records[str(ref)].action == "MOVE"   # each member keeps its own action

    def test_user_decision_defaults_to_empty_string(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].user_decision == ""
        assert records[str(ref)].user_decision == ""

    def test_user_decision_preserved_on_load(self, tmp_path):
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({
                "source_path": str(cand),
                "action": "REVIEW_DUPLICATE",
                "group_id": gid,
                "hamming_distance": 3,
                "user_decision": "delete",
            }),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].user_decision == "delete"

    def test_user_decision_missing_column_migrated(self, tmp_path):
        """Older DBs without user_decision column are migrated automatically."""
        cand = tmp_path / "jdrive" / "a.jpg"
        ref = tmp_path / "takeout" / "a.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            {
                "source_path": str(cand),
                "source_label": "jdrive",
                "dest_path": None,
                "action": "REVIEW_DUPLICATE",
                "hamming_distance": 3,
                "group_id": gid,
                "reason": "near-dup",
                "executed": 0,
            },
            {
                "source_path": str(ref),
                "source_label": "takeout",
                "dest_path": None,
                "action": "MOVE",
                "hamming_distance": None,
                "group_id": gid,
                "reason": "unique",
                "executed": 0,
            },
        ], ddl=_DDL_NO_USER_DECISION)
        records = list(ManifestRepository().load(str(db)))
        assert len(records) == 2
        assert all(r.user_decision == "" for r in records)


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
            _row({"source_path": "/source/a.jpg"}),
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
                  "group_id": None, "hamming_distance": None}),
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
                  "group_id": None, "hamming_distance": None, "action": "MOVE"}),
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
            _row({"source_path": "/source/a.jpg"}),
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
            _row({"source_path": "/source/a.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/source/b.jpg", "group_id": None,
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
            _row({"source_path": "/source/a.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/source/b.jpg", "group_id": None,
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
                  "group_id": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().update_decision(str(db), "/source/a.jpg", "keep")

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/source/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "keep"


class TestBatchUpdateDecisions:
    def test_updates_multiple_rows_in_one_call(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "group_id": None, "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/b.jpg", "group_id": None, "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/c.jpg", "group_id": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().batch_update_decisions(str(db), {"/a.jpg": "delete", "/b.jpg": "keep"})

        conn = sqlite3.connect(db)
        rows = {r[0]: r[1] for r in conn.execute(
            "SELECT source_path, user_decision FROM migration_manifest"
        ).fetchall()}
        conn.close()
        assert rows["/a.jpg"] == "delete"
        assert rows["/b.jpg"] == "keep"
        assert rows["/c.jpg"] == ""  # untouched

    def test_noop_on_empty_dict(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "user_decision": "keep",
                  "group_id": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().batch_update_decisions(str(db), {})

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "keep"  # unchanged


class TestRemoveFromReview:
    """remove_from_review() and the load() filter for user_decision='removed'."""

    def test_marks_user_decision_removed(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().remove_from_review(str(db), ["/a.jpg"])

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "removed"

    def test_multiple_paths_marked(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/b.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/c.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().remove_from_review(str(db), ["/a.jpg", "/c.jpg"])

        conn = sqlite3.connect(db)
        rows = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT source_path, user_decision FROM migration_manifest"
            ).fetchall()
        }
        conn.close()
        assert rows["/a.jpg"] == "removed"
        assert rows["/b.jpg"] == ""      # untouched
        assert rows["/c.jpg"] == "removed"

    def test_load_skips_removed_candidates(self, tmp_path):
        f = tmp_path / "photo.jpg"
        _make_jpeg(f)
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f), "action": "MOVE",
                  "group_id": None, "hamming_distance": None,
                  "user_decision": "removed"}),
        ])
        records = list(ManifestRepository().load(str(db)))
        assert all(r.file_path != str(f) for r in records)

    def test_load_skips_removed_group_member(self, tmp_path):
        """When one group member is removed, it is excluded from load."""
        cand = tmp_path / "cand.jpg"
        ref = tmp_path / "ref.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid}),
            _ref_row({"source_path": str(ref), "group_id": gid, "user_decision": "removed"}),
        ])
        records = list(ManifestRepository().load(str(db)))
        # Only 1 member remains → group has <2 members → neither is yielded
        paths = {r.file_path for r in records}
        assert str(ref) not in paths
        assert str(cand) not in paths  # orphaned single is also skipped

    def test_load_non_removed_rows_unaffected(self, tmp_path):
        """Non-removed group members still load; removed ones do not."""
        f_keep = tmp_path / "keep.jpg"
        f_del = tmp_path / "del.jpg"
        f_other = tmp_path / "other.jpg"
        _make_jpeg(f_keep)
        _make_jpeg(f_del)
        _make_jpeg(f_other)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(f_keep), "action": "REVIEW_DUPLICATE",
                  "group_id": gid, "hamming_distance": 3, "user_decision": ""}),
            _row({"source_path": str(f_other), "action": "MOVE",
                  "group_id": gid, "hamming_distance": None, "user_decision": ""}),
            _row({"source_path": str(f_del), "action": "MOVE",
                  "group_id": None, "hamming_distance": None,
                  "user_decision": "removed"}),
        ])
        records = list(ManifestRepository().load(str(db)))
        paths = {r.file_path for r in records}
        assert str(f_keep) in paths
        assert str(f_other) in paths
        assert str(f_del) not in paths

    def test_removed_candidate_leaves_orphaned_group(self, tmp_path):
        """If one group member is removed, the remaining single member is not yielded
        (a group needs ≥2 active members to be shown in the review UI)."""
        cand = tmp_path / "cand.jpg"
        ref = tmp_path / "ref.jpg"
        _make_jpeg(cand)
        _make_jpeg(ref)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "group_id": gid,
                  "user_decision": "removed"}),
            _ref_row({"source_path": str(ref), "group_id": gid}),
        ])
        records = list(ManifestRepository().load(str(db)))
        paths = {r.file_path for r in records}
        assert str(cand) not in paths  # was removed
        assert str(ref) not in paths   # orphaned single — group < 2 members


class TestMarkExecuted:
    def _read_executed(self, db, path: str) -> int:
        import sqlite3 as _sq
        with _sq.connect(db) as conn:
            row = conn.execute(
                "SELECT executed FROM migration_manifest WHERE source_path = ?", (path,)
            ).fetchone()
        return row[0] if row else -1

    def test_marks_single_path_executed(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().mark_executed(str(db), ["/a.jpg"])
        assert self._read_executed(db, "/a.jpg") == 1

    def test_marks_multiple_paths(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
            _row({"source_path": "/b.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().mark_executed(str(db), ["/a.jpg", "/b.jpg"])
        assert self._read_executed(db, "/a.jpg") == 1
        assert self._read_executed(db, "/b.jpg") == 1

    def test_noop_for_unknown_path(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().mark_executed(str(db), ["/does_not_exist.jpg"])
        assert self._read_executed(db, "/a.jpg") == 0

    def test_does_not_affect_other_rows(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
            _row({"source_path": "/b.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().mark_executed(str(db), ["/a.jpg"])
        assert self._read_executed(db, "/a.jpg") == 1
        assert self._read_executed(db, "/b.jpg") == 0


class TestLoadFromDB:
    """load() reads cached metadata from DB columns instead of filesystem."""

    def _row_with_metadata(self, path: str, **kwargs) -> dict:
        base = {
            "source_path": path,
            "source_label": "jdrive",
            "dest_path": None,
            "action": "MOVE",
            "hamming_distance": None,
            "group_id": None,
            "reason": "unique",
            "executed": 0,
            "user_decision": "",
            "file_size_bytes": None,
            "shot_date": None,
            "creation_date": None,
            "mtime": None,
        }
        return {**base, **kwargs}

    def test_load_uses_db_file_size_not_filesystem(self, tmp_path):
        """When file_size_bytes is stored in DB, getsize() must NOT be called."""
        from unittest.mock import patch
        f = tmp_path / "photo.jpg"
        f2 = tmp_path / "photo2.jpg"
        _make_jpeg(f)
        _make_jpeg(f2)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            self._row_with_metadata(str(f), file_size_bytes=12345, group_id=gid),
            self._row_with_metadata(str(f2), file_size_bytes=999, group_id=gid),
        ], ddl=_DDL_WITH_METADATA)

        with patch("os.path.getsize", side_effect=OSError("blocked")):
            records = list(ManifestRepository().load(str(db)))

        rec = next(r for r in records if r.file_path == str(f))
        assert rec.file_size_bytes == 12345

    def test_load_uses_db_shot_date_not_pillow(self, tmp_path):
        """When shot_date is in DB, Pillow EXIF must NOT be called."""
        from datetime import datetime
        from unittest.mock import patch
        f = tmp_path / "photo.jpg"
        f2 = tmp_path / "photo2.jpg"
        _make_jpeg(f)
        _make_jpeg(f2)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            self._row_with_metadata(str(f),
                action="REVIEW_DUPLICATE",
                group_id=gid,
                hamming_distance=3,
                shot_date="2022-06-15T10:30:00",
                file_size_bytes=100,
                creation_date="2022-01-01T00:00:00",
                mtime="2022-01-01T00:00:00"),
            self._row_with_metadata(str(f2),
                group_id=gid,
                file_size_bytes=100,
                creation_date="2022-01-01T00:00:00",
                mtime="2022-01-01T00:00:00"),
        ], ddl=_DDL_WITH_METADATA)

        with patch("infrastructure.manifest_repository.get_exif_datetime_original",
                   return_value=datetime(2000, 1, 1)):
            records = list(ManifestRepository().load(str(db)))

        paths = {r.file_path for r in records}
        assert str(f) in paths
        rec = next(r for r in records if r.file_path == str(f))
        assert rec.shot_date == datetime(2022, 6, 15, 10, 30, 0)

    def test_load_uses_db_creation_date(self, tmp_path):
        """When creation_date is in DB, getctime() must NOT be called."""
        from datetime import datetime
        from unittest.mock import patch
        f = tmp_path / "photo.jpg"
        f2 = tmp_path / "photo2.jpg"
        _make_jpeg(f)
        _make_jpeg(f2)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            self._row_with_metadata(str(f),
                group_id=gid,
                creation_date="2023-03-01T08:00:00",
                file_size_bytes=100,
                mtime="2023-03-01T08:00:00"),
            self._row_with_metadata(str(f2),
                group_id=gid,
                file_size_bytes=100,
                creation_date="2023-03-01T08:00:00",
                mtime="2023-03-01T08:00:00"),
        ], ddl=_DDL_WITH_METADATA)

        with patch("infrastructure.manifest_repository.get_filesystem_creation_datetime",
                   return_value=datetime(2000, 1, 1)):
            records = list(ManifestRepository().load(str(db)))

        rec = next(r for r in records if r.file_path == str(f))
        assert rec.creation_date == datetime(2023, 3, 1, 8, 0, 0)

    def test_load_uses_db_mtime(self, tmp_path):
        """When mtime is in DB, os.path.getmtime() must NOT be called."""
        from datetime import datetime
        from unittest.mock import patch
        f = tmp_path / "photo.jpg"
        f2 = tmp_path / "photo2.jpg"
        _make_jpeg(f)
        _make_jpeg(f2)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            self._row_with_metadata(str(f),
                group_id=gid,
                mtime="2023-06-01T12:00:00",
                file_size_bytes=100,
                creation_date="2023-01-01T00:00:00"),
            self._row_with_metadata(str(f2),
                group_id=gid,
                file_size_bytes=100,
                creation_date="2023-01-01T00:00:00",
                mtime="2023-01-01T00:00:00"),
        ], ddl=_DDL_WITH_METADATA)

        with patch("os.path.getmtime", return_value=0.0):
            records = list(ManifestRepository().load(str(db)))

        rec = next(r for r in records if r.file_path == str(f))
        assert rec.modified_date == datetime(2023, 6, 1, 12, 0, 0)

    def test_load_falls_back_to_filesystem_when_db_columns_null(self, tmp_path):
        """Old manifests (NULL metadata) still use filesystem reads (regression guard)."""
        f = tmp_path / "photo.jpg"
        f2 = tmp_path / "photo2.jpg"
        _make_jpeg(f)
        _make_jpeg(f2)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            self._row_with_metadata(str(f), group_id=gid),   # all 4 metadata = NULL
            self._row_with_metadata(str(f2), group_id=gid),
        ], ddl=_DDL_WITH_METADATA)

        records = list(ManifestRepository().load(str(db)))
        import os
        rec = next(r for r in records if r.file_path == str(f))
        assert rec.file_size_bytes == os.path.getsize(str(f))

    def test_load_skips_no_existence_check(self, tmp_path):
        """Nonexistent files must be yielded — existence check moved to execute time."""
        f2 = tmp_path / "real.jpg"
        _make_jpeg(f2)
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            self._row_with_metadata(
                "/nonexistent/photo.jpg",
                group_id=gid,
                file_size_bytes=100,
                creation_date="2023-01-01T00:00:00",
                mtime="2023-01-01T00:00:00",
            ),
            self._row_with_metadata(
                str(f2),
                group_id=gid,
                file_size_bytes=200,
                creation_date="2023-01-01T00:00:00",
                mtime="2023-01-01T00:00:00",
            ),
        ], ddl=_DDL_WITH_METADATA)

        records = list(ManifestRepository().load(str(db)))
        paths = {r.file_path for r in records}
        assert "/nonexistent/photo.jpg" in paths


class TestConnectionPragmas:
    """All repository write operations must open connections with WAL + NORMAL."""

    def _journal_mode(self, db) -> str:
        with sqlite3.connect(db) as conn:
            return conn.execute("PRAGMA journal_mode").fetchone()[0]

    def test_wal_enabled_after_batch_update(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().batch_update_decisions(str(db), {"/a.jpg": "keep"})
        assert self._journal_mode(db) == "wal"

    def test_wal_enabled_after_save(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        group = PhotoGroup(group_number=1, items=[
            PhotoRecord(
                group_number=1, is_mark=False, is_locked=False,
                folder_path="", file_path="/a.jpg",
                capture_date=None, modified_date=None, file_size_bytes=0,
                action="MOVE", user_decision="keep",
            )
        ])
        ManifestRepository().save(str(db), [group])
        assert self._journal_mode(db) == "wal"

    def test_wal_enabled_after_mark_executed(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().mark_executed(str(db), ["/a.jpg"])
        assert self._journal_mode(db) == "wal"

    def test_wal_enabled_after_update_decision(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().update_decision(str(db), "/a.jpg", "delete")
        assert self._journal_mode(db) == "wal"

    def test_wal_enabled_after_remove_from_review(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        ManifestRepository().remove_from_review(str(db), ["/a.jpg"])
        assert self._journal_mode(db) == "wal"


class TestSaveUsesExecutemany:
    """save() must delegate to executemany(), not one execute() per row."""

    def test_save_return_count_equals_record_count(self, tmp_path):
        """save() must return the number of records passed, regardless of batch size."""
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
            _row({"source_path": "/b.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
            _row({"source_path": "/c.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        groups = [PhotoGroup(group_number=1, items=[
            PhotoRecord(group_number=1, is_mark=False, is_locked=False,
                        folder_path="", file_path=p,
                        capture_date=None, modified_date=None, file_size_bytes=0,
                        action="MOVE", user_decision="keep")
            for p in ("/a.jpg", "/b.jpg", "/c.jpg")
        ])]
        count = ManifestRepository().save(str(db), groups)
        assert count == 3

    def test_save_empty_groups_returns_zero(self, tmp_path):
        db = _make_manifest(tmp_path, [])
        count = ManifestRepository().save(str(db), [])
        assert count == 0

    def test_save_still_writes_all_decisions(self, tmp_path):
        """Correctness check: executemany saves every record."""
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
            _row({"source_path": "/b.jpg", "action": "MOVE",
                  "group_id": None, "hamming_distance": None}),
        ])
        groups = [PhotoGroup(group_number=1, items=[
            PhotoRecord(group_number=1, is_mark=False, is_locked=False,
                        folder_path="", file_path="/a.jpg",
                        capture_date=None, modified_date=None, file_size_bytes=0,
                        action="MOVE", user_decision="keep"),
            PhotoRecord(group_number=1, is_mark=False, is_locked=False,
                        folder_path="", file_path="/b.jpg",
                        capture_date=None, modified_date=None, file_size_bytes=0,
                        action="MOVE", user_decision="delete"),
        ])]
        count = ManifestRepository().save(str(db), groups)
        assert count == 2

        with sqlite3.connect(db) as conn:
            rows = {r[0]: r[1] for r in conn.execute(
                "SELECT source_path, user_decision FROM migration_manifest"
            ).fetchall()}
        assert rows["/a.jpg"] == "keep"
        assert rows["/b.jpg"] == "delete"


class TestRemoveFromReviewNoVacuum:
    """remove_from_review() must NOT call VACUUM (rows are marked, not deleted)."""

    def test_vacuum_absent_from_source(self):
        """remove_from_review() must not execute VACUUM.

        Rows are marked (UPDATE), not deleted, so VACUUM reclaims nothing —
        it would just be a costly full DB rewrite for zero benefit.
        """
        import ast
        import inspect
        from infrastructure.manifest_repository import ManifestRepository

        src = inspect.getsource(ManifestRepository.remove_from_review)
        # Dedent so ast.parse sees a valid top-level function definition.
        import textwrap
        tree = ast.parse(textwrap.dedent(src))

        # Collect all string constants used as SQL arguments to conn.execute / executemany.
        sql_strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        sql_strings.append(arg.value)

        for sql in sql_strings:
            assert "VACUUM" not in sql.upper(), (
                f"remove_from_review() still executes VACUUM via: {sql!r}"
            )


class TestInGroupRowOrdering:
    """#55 — KEEP (the reference/primary file) sits at the top of its group."""

    def test_keep_appears_before_review_duplicate_and_exact(self, tmp_path):
        ref = tmp_path / "ref" / "ref.jpg"
        review = tmp_path / "review" / "near.jpg"
        exact = tmp_path / "exact" / "dup.jpg"
        for p in (ref, review, exact):
            _make_jpeg(p)
        gid = "/group/order-test"
        db = _make_manifest(tmp_path, [
            # Insert in non-priority order; SQL ordering should still put KEEP first.
            _row({"source_path": str(review), "action": "REVIEW_DUPLICATE", "group_id": gid}),
            _row({"source_path": str(exact), "action": "EXACT", "group_id": gid,
                  "hamming_distance": None}),
            _row({"source_path": str(ref), "action": "KEEP", "group_id": gid,
                  "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        actions = [r.action for r in records]
        # KEEP must be first; the rest follow per the documented order.
        assert actions[0] == "KEEP", \
            f"reference file should be at top of group; got order {actions}"
        assert actions == ["KEEP", "REVIEW_DUPLICATE", "EXACT"]
