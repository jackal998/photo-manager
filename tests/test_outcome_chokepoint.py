"""A4 — outcome writes through the guarded chokepoint (design section 5, 6, 7).

Step 1 of A4 (BE-15, BE-16) pins behaviour that exists on master today: they
must pass on the base commit and on the A4 head alike. Later steps add the
red tests for the stale-plan, SQL-guard and record_deleted acceptance items.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import infrastructure.logging as log_module
from core.app_service.execute_service import (
    execute_decisions,
    prune_singletons,
    remove_from_review,
    restore_to_review,
)
from infrastructure.manifest_repository import ManifestRepository, ManifestTxn
from scanner.manifest import _DDL as _MANIFEST_DDL


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
            row = {"source_label": "test", "action": "", **r}
            cols = list(row.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            conn.execute(
                f"INSERT INTO migration_manifest ({', '.join(cols)}) VALUES ({placeholders})",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _read_row(manifest: Path, file_path: str) -> tuple:
    conn = sqlite3.connect(str(manifest))
    try:
        return conn.execute(
            "SELECT outcome, executed, is_locked, user_decision FROM migration_manifest "
            "WHERE source_path = ?",
            (file_path,),
        ).fetchone()
    finally:
        conn.close()


def _touch(tmp_path: Path, name: str) -> Path:
    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    path = files_dir / name
    path.write_bytes(b"x" * 64)
    return path


def _delete_csvs() -> list[Path]:
    """Audit CSVs in the (conftest-redirected) delete-log directory."""
    log_dir = Path(log_module.get_delete_log_directory())
    if not log_dir.exists():
        return []
    return sorted(log_dir.glob("delete_*.csv"))


# ---------------------------------------------------------------------------
# BE-15: file deleted, DB write failed -> still a success, listed in
# db_write_failed (execute_service.py per-file branch).
# ---------------------------------------------------------------------------


class TestBE15FileDeletedButOutcomeWriteFails:
    def test_deleted_file_with_failed_outcome_write_is_success_and_db_write_failed(
        self, tmp_path, monkeypatch
    ):
        victim = _touch(tmp_path, "victim.jpg")
        keeper = _touch(tmp_path, "keeper.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(victim), "group_id": "g1", "outcome": "",
             "user_decision": "delete", "file_size_bytes": 64},
            {"source_path": str(keeper), "group_id": "g1", "outcome": "",
             "user_decision": "", "file_size_bytes": 64},
        ])

        def _disk_full(*_args, **_kwargs):
            raise OSError("disk full")

        # The per-file outcome write: finalize_outcome on master, record_deleted
        # after A4. Both are patched so the pin does not depend on which one runs.
        monkeypatch.setattr(ManifestRepository, "finalize_outcome", _disk_full, raising=False)

        monkeypatch.setattr(ManifestTxn, "record_deleted", _disk_full, raising=False)

        result = execute_decisions(str(manifest), recycle=False)

        assert not victim.exists(), "the file is gone from disk"
        assert str(victim) in result["success_paths"]
        assert result["db_write_failed"] == [str(victim)]
        assert str(keeper) not in result["success_paths"]


# ---------------------------------------------------------------------------
# BE-16: Skip now, prune and restore write no audit CSV, on purpose.
# Positive control: an Execute delete DOES write one, so the glob can see it.
# ---------------------------------------------------------------------------


class TestBE16OutcomeVerbsWriteNoAuditCsv:
    def test_execute_delete_writes_an_audit_csv_positive_control(self, tmp_path):
        victim = _touch(tmp_path, "victim.jpg")
        keeper = _touch(tmp_path, "keeper.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(victim), "group_id": "g1", "outcome": "",
             "user_decision": "delete", "file_size_bytes": 64},
            {"source_path": str(keeper), "group_id": "g1", "outcome": "",
             "user_decision": "", "file_size_bytes": 64},
        ])

        execute_decisions(str(manifest), recycle=False)

        assert len(_delete_csvs()) == 1

    def test_skip_now_writes_no_delete_csv(self, tmp_path):
        f1 = _touch(tmp_path, "a.jpg")
        f2 = _touch(tmp_path, "b.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "outcome": ""},
            {"source_path": str(f2), "group_id": "g1", "outcome": ""},
        ])

        remove_from_review(str(manifest), [str(f1)])

        assert _read_row(manifest, str(f1))[0] == "ignored"
        assert _delete_csvs() == []

    def test_restore_writes_no_delete_csv(self, tmp_path):
        f1 = _touch(tmp_path, "a.jpg")
        f2 = _touch(tmp_path, "b.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "outcome": "ignored", "executed": 0},
            {"source_path": str(f2), "group_id": "g1", "outcome": ""},
        ])

        restore_to_review(str(manifest), [str(f1)])

        assert _read_row(manifest, str(f1))[0] == ""
        assert _delete_csvs() == []

    def test_prune_writes_no_delete_csv(self, tmp_path):
        lone = _touch(tmp_path, "lone.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(lone), "group_id": "g9", "outcome": ""},
        ])

        result = prune_singletons(str(manifest))

        assert result["pruned"] == [str(lone)]
        assert _read_row(manifest, str(lone))[0] == "ignored"
        assert _delete_csvs() == []
