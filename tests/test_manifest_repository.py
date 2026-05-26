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

    def test_legacy_user_decision_keep_round_trips(self, tmp_path):
        """#425 — back-compat: the repository must still accept the
        legacy literal "keep" string on save and round-trip it back
        from disk unchanged. Canonical write paths use "" but old
        manifests on disk may still carry the literal value, and
        operations that re-save them must preserve the data."""
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
        # #425 — first record canonical empty keep; second deletes.
        group = PhotoGroup(group_number=1, items=[
            self._make_record("/source/a.jpg", "MOVE", ""),
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
        # #425 — flipped "keep" → "" (canonical keep). The test proves
        # that an existing "delete" decision can be overwritten back to
        # the canonical undecided/keep state.
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/source/a.jpg", "user_decision": "delete",
                  "group_id": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().update_decision(str(db), "/source/a.jpg", "")

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/source/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == ""


class TestBatchUpdateDecisions:
    def test_updates_multiple_rows_in_one_call(self, tmp_path):
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "group_id": None, "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/b.jpg", "group_id": None, "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/c.jpg", "group_id": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        # #425 — second value flipped "keep" → REMOVE_FROM_LIST_DECISION
        # so the batch test still verifies two distinct non-default
        # writes (canonical keep "" would be indistinguishable from
        # the untouched /c.jpg row).
        from app.views.constants import REMOVE_FROM_LIST_DECISION
        ManifestRepository().batch_update_decisions(str(db), {"/a.jpg": "delete", "/b.jpg": REMOVE_FROM_LIST_DECISION})

        conn = sqlite3.connect(db)
        rows = {r[0]: r[1] for r in conn.execute(
            "SELECT source_path, user_decision FROM migration_manifest"
        ).fetchall()}
        conn.close()
        assert rows["/a.jpg"] == "delete"
        assert rows["/b.jpg"] == REMOVE_FROM_LIST_DECISION
        assert rows["/c.jpg"] == ""  # untouched

    def test_noop_on_empty_dict(self, tmp_path):
        # #425 — flipped "keep" → "delete" so the unchanged-state
        # assertion is distinguishable from the schema default.
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "user_decision": "delete",
                  "group_id": None, "hamming_distance": None, "action": "MOVE"}),
        ])
        ManifestRepository().batch_update_decisions(str(db), {})

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = '/a.jpg'"
        ).fetchone()
        conn.close()
        assert row[0] == "delete"  # unchanged


class TestBatchUpdateDecisionsAndLock:
    """Combined batch method — used by the auto-select post-scan write
    path (#393) to land both writes in a single transaction.
    """

    def test_writes_both_decision_and_lock_in_one_call(self, tmp_path):
        """Catches: the combined method drops one side (e.g. forgets
        the lock executemany, or commits before the second statement).
        Mixing keepers (keep+lock=1) with non-keepers (delete only,
        lock untouched) exercises both columns and confirms they
        don't bleed onto each other.
        """
        db = _make_manifest(tmp_path, [
            _row({"source_path": "/k.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/d.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
            _row({"source_path": "/untouched.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
        ])
        # Add is_locked column for parity with the production schema
        # path (auto-select migrates lazily before this call).
        ManifestRepository().ensure_schema(str(db))

        # #425 — canonical empty-keep write matches the auto-select
        # production path post-canonicalisation.
        ManifestRepository().batch_update_decisions_and_lock(
            str(db),
            decisions={"/k.jpg": "", "/d.jpg": "delete"},
            lock_states={"/k.jpg": True},
        )

        conn = sqlite3.connect(db)
        try:
            rows = {
                r[0]: (r[1], r[2]) for r in conn.execute(
                    "SELECT source_path, user_decision, is_locked "
                    "FROM migration_manifest"
                ).fetchall()
            }
        finally:
            conn.close()
        # Keeper: empty decision + lock badge.
        assert rows["/k.jpg"] == ("", 1)
        assert rows["/d.jpg"] == ("delete", 0)
        assert rows["/untouched.jpg"] == ("", 0)

    def test_both_empty_short_circuits_without_opening_connection(
        self, tmp_path, monkeypatch
    ):
        """Catches: the short-circuit guard regresses and we open a
        connection (+ optionally commit an empty transaction) when
        there's literally nothing to write. The auto-select caller
        invokes unconditionally on empty-keepers scans; this must
        stay free.
        """
        from infrastructure import manifest_repository as repo_mod

        db = _make_manifest(tmp_path, [
            _row({"source_path": "/a.jpg", "group_id": None,
                  "hamming_distance": None, "action": "MOVE"}),
        ])
        opens: list[str] = []
        real_connect = repo_mod._connect

        def _spy(path):
            opens.append(path)
            return real_connect(path)

        monkeypatch.setattr(repo_mod, "_connect", _spy)
        ManifestRepository().batch_update_decisions_and_lock(
            str(db), decisions={}, lock_states={}
        )
        assert opens == []


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
        # #425 — flipped "keep" → "delete" (canonical keep "" would be
        # the schema default — this proves WAL is set even for a write
        # that changes a row to a distinct non-default value).
        ManifestRepository().batch_update_decisions(str(db), {"/a.jpg": "delete"})
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
                action="MOVE", user_decision="",  # #425 — canonical keep
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
                        action="MOVE", user_decision="")  # #425 canonical keep
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
                        action="MOVE", user_decision=""),  # #425 canonical keep
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
        assert rows["/a.jpg"] == ""  # #425 canonical keep
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
    """#55 + #76 — the file rendered as "Ref" sits at the top of its group.

    `_file_similarity` (in `app/views/tree_model_builder.py`) renders any
    action other than EXACT and REVIEW_DUPLICATE as "Ref". So the SQL
    ordering puts every "Ref tier" action (KEEP / MOVE / UNDATED / unset)
    at position 1, then EXACT (2 — strongest match), then REVIEW_DUPLICATE
    (3 — weaker), so a group reads top-down as Ref → 100% → near-matches.
    """

    def test_move_primary_appears_before_review_duplicate_and_exact(self, tmp_path):
        """The s07/s10 case: dedup classifier labels the primary as MOVE.

        Regression for #76. The original #55 fix moved only KEEP to position 1,
        but `dedup.classify` actually assigns MOVE to most real-world primaries
        — so KEEP-only didn't move the displayed Ref to the top in practice.
        """
        ref = tmp_path / "ref" / "primary.jpg"
        review = tmp_path / "review" / "near.jpg"
        exact = tmp_path / "exact" / "dup.jpg"
        for p in (ref, review, exact):
            _make_jpeg(p)
        gid = "/group/move-primary"
        db = _make_manifest(tmp_path, [
            # Insert in non-priority order; SQL ordering must still put MOVE first.
            _row({"source_path": str(review), "action": "REVIEW_DUPLICATE", "group_id": gid}),
            _row({"source_path": str(exact), "action": "EXACT", "group_id": gid,
                  "hamming_distance": None}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        actions = [r.action for r in records]
        assert actions[0] == "MOVE", \
            f"MOVE primary (rendered as Ref) should be at top of group; got order {actions}"
        assert actions == ["MOVE", "EXACT", "REVIEW_DUPLICATE"]

    def test_keep_primary_appears_before_review_duplicate_and_exact(self, tmp_path):
        """KEEP primary case (rarer in practice) — also a Ref tier action."""
        ref = tmp_path / "ref" / "ref.jpg"
        review = tmp_path / "review" / "near.jpg"
        exact = tmp_path / "exact" / "dup.jpg"
        for p in (ref, review, exact):
            _make_jpeg(p)
        gid = "/group/keep-primary"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(review), "action": "REVIEW_DUPLICATE", "group_id": gid}),
            _row({"source_path": str(exact), "action": "EXACT", "group_id": gid,
                  "hamming_distance": None}),
            _row({"source_path": str(ref), "action": "KEEP", "group_id": gid,
                  "hamming_distance": None}),
        ])
        records = list(ManifestRepository().load(str(db)))
        actions = [r.action for r in records]
        assert actions[0] == "KEEP", \
            f"KEEP primary (rendered as Ref) should be at top of group; got order {actions}"
        assert actions == ["KEEP", "EXACT", "REVIEW_DUPLICATE"]


# ---------------------------------------------------------------------------
# is_locked persistence (photo-manager#164)
# ---------------------------------------------------------------------------

_DDL_WITH_IS_LOCKED = """
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
    is_locked        INTEGER NOT NULL DEFAULT 0
);
"""


class TestIsLockedPersistence:
    """Round-trip the is_locked column: write via batch_update_lock_state,
    read back via load(). Pre-existing DBs (no is_locked column) auto-migrate
    via the additive ALTER TABLE list and load with is_locked=False.
    """

    def test_is_locked_default_false_for_new_rows(self, tmp_path):
        """A row inserted without is_locked loads with is_locked=False."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "REVIEW_DUPLICATE",
                  "group_id": gid}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ])
        records = {str(r.file_path): r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].is_locked is False
        assert records[str(ref)].is_locked is False

    def test_old_db_without_is_locked_column_auto_migrates(self, tmp_path):
        """Pre-#164 DBs lack the is_locked column. The additive migration
        list adds it on load with default 0, so old manifests open
        without error and every row reads back as is_locked=False."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "REVIEW_DUPLICATE",
                  "group_id": gid}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ], ddl=_DDL_WITH_METADATA)  # _DDL_WITH_METADATA has no is_locked
        # First load triggers the migration (ALTER TABLE ADD COLUMN).
        records = list(ManifestRepository().load(str(db)))
        assert all(r.is_locked is False for r in records)
        # Re-open and confirm the column now exists.
        conn = sqlite3.connect(db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(migration_manifest)")}
        conn.close()
        assert "is_locked" in cols

    def test_batch_update_lock_state_writes_and_loads(self, tmp_path):
        """batch_update_lock_state flips the column; subsequent load()
        returns is_locked=True for the touched row."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "REVIEW_DUPLICATE",
                  "group_id": gid}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ], ddl=_DDL_WITH_IS_LOCKED)

        ManifestRepository().batch_update_lock_state(
            str(db), {str(cand): True}
        )

        records = {str(r.file_path): r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].is_locked is True
        assert records[str(ref)].is_locked is False

    def test_batch_update_lock_state_handles_unlock(self, tmp_path):
        """Re-running batch_update_lock_state with locked=False clears it."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "REVIEW_DUPLICATE",
                  "group_id": gid, "is_locked": 1}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ], ddl=_DDL_WITH_IS_LOCKED)

        # Sanity: lock loaded back as True
        records = {str(r.file_path): r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].is_locked is True

        # Unlock
        ManifestRepository().batch_update_lock_state(
            str(db), {str(cand): False}
        )

        records = {str(r.file_path): r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].is_locked is False

    def test_batch_update_lock_state_empty_dict_is_noop(self, tmp_path):
        """Empty input must not error or open a connection."""
        db = tmp_path / "noop.sqlite"
        # File doesn't even exist; empty dict should be a guard-clause early return
        ManifestRepository().batch_update_lock_state(str(db), {})
        assert not db.exists()


# ---------------------------------------------------------------------------
# Scoring system schema migration (photo-manager#187 — PR 1)
# ---------------------------------------------------------------------------


class TestScoringSchemaMigration:
    """Old DBs lacking exif_tag_count / gps_present / xmp_derived / score must
    auto-migrate on load. New manifests get the columns from the base DDL;
    older manifests get them via the additive ALTER TABLE list.

    These four columns are added together in PR 1 (#187) so the scoring
    system has somewhere to write its raw signals and composite score.
    Old manifests load with gps_present=0, xmp_derived=0, exif_tag_count=NULL,
    score=NULL — all of which the scorer interprets as 'no signal' (0.0).
    """

    def test_old_db_without_scoring_columns_auto_migrates(self, tmp_path):
        """An old DB without any of the four scoring columns gains them on
        first load. The load itself must not raise; subsequent inspection
        confirms all four columns are present with the expected types."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        # _DDL_WITH_IS_LOCKED predates the scoring columns — same shape as a
        # manifest written before #187.
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "REVIEW_DUPLICATE",
                  "group_id": gid}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ], ddl=_DDL_WITH_IS_LOCKED)

        # First load triggers the migration (ALTER TABLE ADD COLUMN x4).
        list(ManifestRepository().load(str(db)))

        # All four columns now exist.
        conn = sqlite3.connect(db)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(migration_manifest)")}
        finally:
            conn.close()
        for expected in ("exif_tag_count", "gps_present", "xmp_derived", "score"):
            assert expected in cols, f"Migration did not add column: {expected}"

    def test_old_db_scoring_columns_have_safe_defaults(self, tmp_path):
        """After migration, pre-existing rows get the documented defaults:
        gps_present=0, xmp_derived=0, exif_tag_count=NULL, score=NULL.
        These are the 'no signal' values for an unscored manifest."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "REVIEW_DUPLICATE",
                  "group_id": gid}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ], ddl=_DDL_WITH_IS_LOCKED)

        list(ManifestRepository().load(str(db)))  # trigger migration

        conn = sqlite3.connect(db)
        try:
            r = conn.execute(
                "SELECT exif_tag_count, gps_present, xmp_derived, score "
                "FROM migration_manifest"
            ).fetchone()
        finally:
            conn.close()
        # exif_tag_count and score nullable; gps_present and xmp_derived
        # NOT NULL DEFAULT 0 per migration spec.
        assert r == (None, 0, 0, None)

    def test_migration_is_idempotent(self, tmp_path):
        """Loading an already-migrated DB must not error — ALTER TABLE ADD
        COLUMN raises on duplicate columns, which the repository swallows.
        Re-running load() twice exercises that swallow path."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        db = _make_manifest(tmp_path, [
            _row({"source_path": str(cand), "action": "REVIEW_DUPLICATE",
                  "group_id": gid}),
            _row({"source_path": str(ref), "action": "MOVE", "group_id": gid,
                  "hamming_distance": None}),
        ], ddl=_DDL_WITH_IS_LOCKED)

        # First load adds the columns; second load must not raise.
        list(ManifestRepository().load(str(db)))
        list(ManifestRepository().load(str(db)))  # should not raise

    def test_score_loads_from_db_into_photo_record(self, tmp_path):
        """The score column is read from the manifest and threaded onto
        PhotoRecord.score so the UI can display / sort it. PR 5 contract."""
        cand = tmp_path / "a.jpg"
        ref = tmp_path / "b.jpg"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        # Use the production write path so the score column is in the DDL.
        from scanner.dedup import ManifestRow
        from scanner.manifest import write_manifest

        rows = [
            ManifestRow(
                source_path=str(cand), source_label="src",
                dest_path=None, action="REVIEW_DUPLICATE",
                source_hash="aaa", phash=None, hamming_distance=5,
                duplicate_of=None, reason="",
                group_id=gid, score=0.87,
            ),
            ManifestRow(
                source_path=str(ref), source_label="src",
                dest_path=None, action="MOVE",
                source_hash="bbb", phash=None, hamming_distance=None,
                duplicate_of=None, reason="",
                group_id=gid, score=0.42,
            ),
        ]
        db = tmp_path / "m.sqlite"
        write_manifest(rows, db)

        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(cand)].score == pytest.approx(0.87)
        assert records[str(ref)].score == pytest.approx(0.42)

    def test_score_loads_as_none_when_null(self, tmp_path):
        """Live Photo MOV passengers and isolated rows get score=NULL in
        the DB. The load path must preserve None on PhotoRecord, not
        coerce to 0.0 (which would silently re-introduce 'unscored ties
        with worst score' bug)."""
        cand = tmp_path / "a.heic"
        ref = tmp_path / "a.mov"
        cand.write_bytes(b""); ref.write_bytes(b"")
        gid = "/group/a"
        from scanner.dedup import ManifestRow
        from scanner.manifest import write_manifest

        rows = [
            ManifestRow(
                source_path=str(cand), source_label="src",
                dest_path=None, action="MOVE",
                source_hash="aaa", phash=None, hamming_distance=None,
                duplicate_of=None, reason="",
                group_id=gid, score=0.75,
            ),
            ManifestRow(
                source_path=str(ref), source_label="src",
                dest_path=None, action="MOVE",
                source_hash="bbb", phash=None, hamming_distance=None,
                duplicate_of=None, reason="",
                group_id=gid, score=None,  # Live Photo MOV passenger
            ),
        ]
        db = tmp_path / "m.sqlite"
        write_manifest(rows, db)

        records = {r.file_path: r for r in ManifestRepository().load(str(db))}
        assert records[str(ref)].score is None
        assert records[str(cand)].score == pytest.approx(0.75)
