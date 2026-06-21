"""Unit tests for core/app_service/execute_service.py.

These tests exercise the service functions directly (no HTTP layer).
All tests use real sqlite manifests and real temp files — no synthetic
coverage padding.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from core.app_service.execute_service import (
    execute_decisions,
    prune_singletons,
    remove_from_review,
    save_manifest,
)
from infrastructure.manifest_repository import ManifestRepository
from scanner.manifest import _DDL as _MANIFEST_DDL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    manifest = tmp_path / "manifest.sqlite"
    conn = sqlite3.connect(str(manifest))
    try:
        conn.executescript(_MANIFEST_DDL)
        conn.commit()
    finally:
        conn.close()
    ManifestRepository().ensure_schema(str(manifest))

    conn = sqlite3.connect(str(manifest))
    try:
        for r in rows:
            row = {"source_label": "test", **r}
            cols = list(row.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            col_list = ", ".join(cols)
            conn.execute(
                f"INSERT INTO migration_manifest ({col_list}) VALUES ({placeholders})",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _read_col(manifest: Path, file_path: str, col: str) -> object:
    conn = sqlite3.connect(str(manifest))
    try:
        row = conn.execute(
            f"SELECT {col} FROM migration_manifest WHERE source_path = ?",
            (file_path,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _make_real_files(tmp_path: Path, n: int = 2) -> list[Path]:
    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    paths = []
    for i in range(n):
        f = files_dir / f"f{i}.bin"
        f.write_bytes(b"\x55" * 64)
        paths.append(f)
    return paths


# ---------------------------------------------------------------------------
# execute_decisions
# ---------------------------------------------------------------------------


class TestExecuteDecisions:
    def test_delete_decided_removes_file_and_writes_outcome(self, tmp_path):
        """Core D7: outcome='deleted' written per-file right after trash."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False)

        assert set(result["success_paths"]) == {str(f1), str(f2)}
        assert result["failed"] == []
        assert result["missing"] == []
        assert not f1.exists()
        assert not f2.exists()
        assert _read_col(manifest, str(f1), "outcome") == "deleted"
        assert _read_col(manifest, str(f2), "outcome") == "deleted"

    def test_missing_file_in_missing_list_other_succeeds(self, tmp_path):
        """D7 per-file: one missing file in `missing`; the other is still deleted."""
        files = _make_real_files(tmp_path, 2)
        f_ok, f_gone = files
        f_gone.unlink()  # simulate pre-deleted file

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f_ok),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f_gone),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False)

        assert str(f_ok) in result["success_paths"]
        assert str(f_gone) in result["missing"]
        assert str(f_gone) not in result["success_paths"]
        assert _read_col(manifest, str(f_ok), "outcome") == "deleted"

    def test_locked_rows_raise_without_force(self, tmp_path):
        """Locked delete-decided rows raise ValueError('locked_paths', [...])."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "is_locked": 1,
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        with pytest.raises(ValueError) as exc_info:
            execute_decisions(str(manifest), recycle=False, force_locked=False)

        args = exc_info.value.args
        assert args[0] == "locked_paths"
        assert str(f1) in args[1]
        # Nothing deleted.
        assert f1.exists()
        assert f2.exists()

    def test_force_locked_unlocks_and_deletes(self, tmp_path):
        """force_locked=True clears is_locked and deletes the file."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "is_locked": 1,
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False, force_locked=True)

        assert str(f1) in result["success_paths"]
        assert str(f2) in result["success_paths"]
        assert not f1.exists()
        assert not f2.exists()
        assert _read_col(manifest, str(f1), "is_locked") == 0

    def test_ignore_decided_not_deleted_from_disk(self, tmp_path):
        """ignore-decided files get outcome='ignored' but files stay on disk."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False)

        assert set(result["ignored"]) == {str(f1), str(f2)}
        assert result["success_paths"] == []
        assert f1.exists()
        assert f2.exists()
        assert _read_col(manifest, str(f1), "outcome") == "ignored"
        assert _read_col(manifest, str(f2), "outcome") == "ignored"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            execute_decisions(str(tmp_path / "nonexistent.sqlite"))

    def test_scope_paths_filters_to_subset(self, tmp_path):
        """scope_paths limits execution to specified files only."""
        files = _make_real_files(tmp_path, 3)
        f1, f2, f3 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f3),
                "action": "",
                "group_id": "g2",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        # Only execute f1, skip f2 and f3.
        result = execute_decisions(
            str(manifest),
            scope_paths=[str(f1)],
            recycle=False,
        )

        assert str(f1) in result["success_paths"]
        assert str(f2) not in result["success_paths"]
        assert str(f3) not in result["success_paths"]
        assert not f1.exists()
        assert f2.exists()   # not in scope
        assert f3.exists()   # not in scope


# ---------------------------------------------------------------------------
# remove_from_review
# ---------------------------------------------------------------------------


class TestRemoveFromReview:
    def test_removes_sets_ignored_outcome(self, tmp_path):
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        result = remove_from_review(str(manifest), [str(f1), str(f2)])

        assert result["removed"] == 2
        assert f1.exists()   # files untouched
        assert f2.exists()
        assert _read_col(manifest, str(f1), "outcome") == "ignored"
        assert _read_col(manifest, str(f2), "outcome") == "ignored"

    def test_locked_row_raises_without_force(self, tmp_path):
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 1,
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        with pytest.raises(ValueError) as exc_info:
            remove_from_review(str(manifest), [str(f1), str(f2)], force_locked=False)

        assert exc_info.value.args[0] == "locked_paths"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            remove_from_review(str(tmp_path / "no.sqlite"), ["x"])


# ---------------------------------------------------------------------------
# prune_singletons
# ---------------------------------------------------------------------------


class TestPruneSingletons:
    def _singleton_manifest(self, tmp_path: Path) -> tuple[Path, Path]:
        """One singleton (partner deleted) and returns (manifest, f1)."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])
        return manifest, f1

    def test_plain_singleton_pruned(self, tmp_path):
        manifest, f1 = self._singleton_manifest(tmp_path)
        result = prune_singletons(str(manifest))
        assert str(f1) in result["pruned"]
        assert result["locked_skipped"] == []
        assert _read_col(manifest, str(f1), "outcome") == "ignored"

    def test_locked_singleton_in_locked_skipped(self, tmp_path):
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 1,
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = prune_singletons(str(manifest))
        assert result["pruned"] == []
        assert str(f1) in result["locked_skipped"]
        assert _read_col(manifest, str(f1), "outcome") == ""  # untouched

    def test_actioned_singleton_excluded_without_include_actioned(self, tmp_path):
        """Singleton with decision='delete' is NOT pruned when include_actioned=False."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",  # actioned singleton
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = prune_singletons(str(manifest), include_actioned=False)
        assert result["pruned"] == []

    def test_actioned_singleton_included_with_include_actioned(self, tmp_path):
        """Singleton with decision='delete' IS pruned when include_actioned=True."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",  # actioned singleton
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = prune_singletons(str(manifest), include_actioned=True)
        assert str(f1) in result["pruned"]

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            prune_singletons(str(tmp_path / "no.sqlite"))


# ---------------------------------------------------------------------------
# save_manifest
# ---------------------------------------------------------------------------


class TestSaveManifest:
    def test_in_place_save_writes_decisions(self, tmp_path):
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        result = save_manifest(str(manifest))
        assert result["saved_to"] == str(manifest)
        # FIX 4: updated is now the count of in-review (outcome='') rows.
        assert isinstance(result["updated"], int)
        assert result["updated"] >= 1

        # Verify the decision is still persisted (it was written per-PATCH).
        assert _read_col(manifest, str(f1), "user_decision") == "delete"

    def test_save_as_creates_copy_with_schema(self, tmp_path):
        """save-as copies the manifest; the copy carries the same decisions."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        # Write a decision directly so we can verify the copy carries it.
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])
        target = tmp_path / "copy.sqlite"

        result = save_manifest(str(manifest), str(target))
        assert result["saved_to"] == str(target)
        assert target.exists()

        # Verify the copy carries the decision row by opening it fresh.
        copy_repo = ManifestRepository()
        conn = sqlite3.connect(str(target))
        try:
            count_row = conn.execute(
                "SELECT COUNT(*) FROM migration_manifest"
            ).fetchone()
            decision_row = conn.execute(
                "SELECT user_decision FROM migration_manifest WHERE source_path = ?",
                (str(f1),),
            ).fetchone()
        finally:
            conn.close()
        assert count_row[0] >= 2
        # FIX 4: copy2 carries the decisions because WAL was checkpointed first.
        assert decision_row is not None
        assert decision_row[0] == "delete"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            save_manifest(str(tmp_path / "no.sqlite"))


# ---------------------------------------------------------------------------
# FIX 1 — out-of-root source_path rows are refused
# ---------------------------------------------------------------------------


class TestExecuteDecisionsOutOfRoot:
    """FIX 1 REGRESSION TEST: out-of-root source_path rows must be refused."""

    def test_out_of_root_delete_row_is_refused_file_survives(self, tmp_path):
        """SHIP-BLOCKER test: a source_path outside allowed_roots must NOT be deleted.

        Build a manifest under tmp_path/manifest_dir (which is an allowed root).
        That manifest contains a source_path pointing to tmp_path/outside_dir
        (which is NOT in allowed_roots).  Create the out-of-root file on disk.
        Run execute_decisions with allowed_roots=[manifest_dir].
        Assert:
        - The out-of-root file still exists on disk.
        - Its DB outcome is still '' (not 'deleted').
        - It appears in the returned 'failed' list with reason 'outside_allowed_roots'.
        """
        # Two separate directories: one allowed, one not.
        manifest_dir = tmp_path / "allowed"
        manifest_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        # A real file inside the allowed root (will be deleted normally).
        inside_file = manifest_dir / "inside.bin"
        inside_file.write_bytes(b"\xAA" * 64)

        # A real file OUTSIDE the allowed root (must survive).
        outside_file = outside_dir / "sensitive.bin"
        outside_file.write_bytes(b"\xBB" * 64)

        manifest = _make_manifest(manifest_dir, [
            {
                "source_path": str(inside_file),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(outside_file),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(
            str(manifest),
            recycle=False,
            allowed_roots=[str(manifest_dir)],
        )

        # The inside file is gone (normal delete).
        assert not inside_file.exists()
        assert str(inside_file) in result["success_paths"]

        # The outside file MUST still exist.
        assert outside_file.exists(), (
            "out-of-root file was deleted — FIX 1 not applied"
        )

        # DB outcome for the out-of-root path must still be '' (untouched).
        assert _read_col(manifest, str(outside_file), "outcome") == "", (
            "out-of-root file's DB outcome was written — FIX 1 not applied"
        )

        # The out-of-root path must appear in failed with the correct reason.
        failed_paths = {entry[0]: entry[1] for entry in result["failed"]}
        assert str(outside_file) in failed_paths, (
            "out-of-root path not in failed list"
        )
        assert failed_paths[str(outside_file)] == "outside_allowed_roots", (
            f"unexpected reason: {failed_paths[str(outside_file)]!r}"
        )

    def test_out_of_root_ignore_row_is_skipped(self, tmp_path):
        """Out-of-root ignore-decided rows are silently skipped (not finalized)."""
        manifest_dir = tmp_path / "allowed"
        manifest_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        inside_file = manifest_dir / "inside.bin"
        inside_file.write_bytes(b"\xAA" * 64)
        outside_file = outside_dir / "outside.bin"
        outside_file.write_bytes(b"\xBB" * 64)

        manifest = _make_manifest(manifest_dir, [
            {
                "source_path": str(inside_file),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(outside_file),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(
            str(manifest),
            recycle=False,
            allowed_roots=[str(manifest_dir)],
        )

        # Inside file is correctly ignored.
        assert str(inside_file) in result["ignored"]
        assert _read_col(manifest, str(inside_file), "outcome") == "ignored"

        # Outside file is NOT in ignored and its DB outcome is still ''.
        assert str(outside_file) not in result["ignored"]
        assert _read_col(manifest, str(outside_file), "outcome") == ""

    def test_no_allowed_roots_skips_safety_check(self, tmp_path):
        """When allowed_roots is None, the safety check is skipped (legacy behavior)."""
        files = _make_real_files(tmp_path, 1)
        f = files[0]

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False, allowed_roots=None)
        assert str(f) in result["success_paths"]
        assert not f.exists()
