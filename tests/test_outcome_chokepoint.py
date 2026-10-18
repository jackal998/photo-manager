"""A4 — outcome writes through the guarded chokepoint (design section 5, 6, 7).

Step 1 of A4 (BE-15, BE-16) pins behaviour that exists on master today: they
must pass on the base commit and on the A4 head alike. Later steps add the
red tests for the stale-plan, SQL-guard and record_deleted acceptance items.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import core.app_service.execute_service as execute_module
import infrastructure.logging as log_module
from core.app_service.execute_service import (
    execute_decisions,
    prune_singletons,
    remove_from_review,
    restore_to_review,
)
from infrastructure.manifest_repository import (
    ManifestRepository,
    ManifestTxn,
    _open_txn_paths,
    _txn_key,
)
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


# ---------------------------------------------------------------------------
# A4 red tests. Each one fails on master: the stale plan is the lock that
# lands AFTER the verb's plan read and BEFORE its write. The hook below sets
# that lock just before the write runs, on master's write method and on the
# A4 ManifestTxn.transition alike (only the ones that exist are wrapped).
# ---------------------------------------------------------------------------

_LOCK_SQL = "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?"


def _set_locked(manifest: Path, path: str) -> None:
    conn = sqlite3.connect(str(manifest))
    try:
        conn.execute(_LOCK_SQL, (path,))
        conn.commit()
    finally:
        conn.close()


def _lock_before_write(monkeypatch, manifest: Path, path: str) -> None:
    """Lock ``path`` just before a write method runs (the stale-plan window)."""

    def _hooked(owner, name):
        real = getattr(owner, name, None)
        if real is None:
            return  # master has no ManifestTxn.transition, A4 has no ManifestRepository.X

        def _wrapper(self, *args, **kwargs):
            if isinstance(self, ManifestTxn):
                self._conn.execute(_LOCK_SQL, (path,))  # same connection, open txn
            else:
                _set_locked(manifest, path)
            return real(self, *args, **kwargs)

        monkeypatch.setattr(owner, name, _wrapper)

    for owner, name in _WRITE_METHODS:
        _hooked(owner, name)


_WRITE_METHODS = (
    (ManifestRepository, "remove_from_review"),
    (ManifestRepository, "restore_to_review"),
    (ManifestRepository, "finalize_outcome"),
    (ManifestTxn, "transition"),
)


class TestStalePlanCannotWriteLockedRow:
    """A lock set after the service's plan and before its write: refused (409)."""

    def test_skip_now_refuses_a_row_locked_after_the_plan(self, tmp_path, monkeypatch):
        f1, f2 = _touch(tmp_path, "a.jpg"), _touch(tmp_path, "b.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "outcome": ""},
            {"source_path": str(f2), "group_id": "g1", "outcome": ""},
        ])
        _lock_before_write(monkeypatch, manifest, str(f1))

        with pytest.raises(ValueError) as exc:
            remove_from_review(str(manifest), [str(f1)])

        assert exc.value.args[0] == "locked_paths"
        assert exc.value.args[1] == [str(f1)]
        assert _read_row(manifest, str(f1))[:3] == ("", 0, 1)

    def test_restore_refuses_a_dismissed_row_locked_after_the_plan(
        self, tmp_path, monkeypatch
    ):
        f1, f2 = _touch(tmp_path, "a.jpg"), _touch(tmp_path, "b.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "outcome": "ignored", "executed": 0},
            {"source_path": str(f2), "group_id": "g1", "outcome": ""},
        ])
        _lock_before_write(monkeypatch, manifest, str(f1))

        with pytest.raises(ValueError) as exc:
            restore_to_review(str(manifest), [str(f1)])

        assert exc.value.args[0] == "locked_paths"
        assert _read_row(manifest, str(f1))[:3] == ("ignored", 0, 1)

    def test_prune_refuses_a_singleton_locked_after_the_plan(self, tmp_path, monkeypatch):
        lone = _touch(tmp_path, "lone.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(lone), "group_id": "g9", "outcome": ""},
        ])
        _lock_before_write(monkeypatch, manifest, str(lone))

        with pytest.raises(ValueError) as exc:
            prune_singletons(str(manifest))

        assert exc.value.args[0] == "locked_paths"
        assert _read_row(manifest, str(lone))[:3] == ("", 0, 1)

    def test_execute_gate_refuses_a_delete_row_the_plan_saw_as_unlocked(
        self, tmp_path, monkeypatch
    ):
        victim, keeper = _touch(tmp_path, "v.jpg"), _touch(tmp_path, "k.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(victim), "group_id": "g1", "outcome": "",
             "user_decision": "delete", "is_locked": 1},
            {"source_path": str(keeper), "group_id": "g1", "outcome": ""},
        ])
        real_load = execute_module._load_decided_rows

        def _stale_plan(manifest_path, scope_paths):
            deletes, ignores, locked_d, locked_i = real_load(manifest_path, scope_paths)
            return deletes + locked_d, ignores + locked_i, [], []  # plan saw no locks

        monkeypatch.setattr(execute_module, "_load_decided_rows", _stale_plan)

        with pytest.raises(ValueError) as exc:
            execute_decisions(str(manifest), recycle=False)

        assert exc.value.args[0] == "locked_paths"
        assert victim.exists(), "a refused Execute deletes nothing"
        assert _read_row(manifest, str(victim))[:3] == ("", 0, 1)

    def test_execute_skip_batch_leaves_a_row_locked_after_the_gate(
        self, tmp_path, monkeypatch
    ):
        ignored_row, keeper = _touch(tmp_path, "i.jpg"), _touch(tmp_path, "k.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(ignored_row), "group_id": "g1", "outcome": "",
             "user_decision": "ignore"},
            {"source_path": str(keeper), "group_id": "g1", "outcome": ""},
        ])
        _lock_before_write(monkeypatch, manifest, str(ignored_row))

        result = execute_decisions(str(manifest), recycle=False)

        assert str(ignored_row) not in result["ignored"]
        assert str(ignored_row) in [p for p, _reason in result["failed"]]
        assert _read_row(manifest, str(ignored_row))[:3] == ("", 0, 1)


class TestGuardedOutcomeSql:
    """Gap (i): the guarded UPDATE on its own, with the refusal switched off."""

    def test_guarded_update_leaves_a_locked_row_untouched_when_not_confirmed(
        self, tmp_path, monkeypatch
    ):
        f1 = _touch(tmp_path, "a.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "outcome": "", "is_locked": 1},
        ])
        monkeypatch.setattr(ManifestTxn, "refuse_unconfirmed", lambda *a, **k: None)

        with ManifestRepository().write_txn(str(manifest)) as tx:
            changed = tx.transition(
                [str(f1)], from_outcome="", to_outcome="ignored",
                confirmed=set(), lock_after=False,
            )

        assert changed == 0
        assert _read_row(manifest, str(f1))[:3] == ("", 0, 1)


class TestTxnMarkerAndTransition:
    """Gap (ii) and the BE-7 refusal scope for transition."""

    def test_open_txn_marker_is_cleared_after_an_exception(self, tmp_path):
        manifest = _make_manifest(tmp_path, [])
        repo = ManifestRepository()

        with pytest.raises(RuntimeError, match="boom"):
            with repo.write_txn(str(manifest)):
                raise RuntimeError("boom")

        assert _txn_key(str(manifest)) not in _open_txn_paths()
        assert list(repo.load(str(manifest))) == []  # no RuntimeError: marker cleared

    def test_transition_from_ignored_does_not_refuse_a_locked_row_in_review(
        self, tmp_path
    ):
        f1, f2 = _touch(tmp_path, "a.jpg"), _touch(tmp_path, "b.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "outcome": "", "is_locked": 1},
            {"source_path": str(f2), "group_id": "g1", "outcome": "ignored", "executed": 0},
        ])

        with ManifestRepository().write_txn(str(manifest)) as tx:
            changed = tx.transition(
                [str(f1)], from_outcome="ignored", to_outcome="",
                confirmed=set(), lock_after=False,
            )

        assert changed == 0
        assert _read_row(manifest, str(f1))[:3] == ("", 0, 1)


class TestRecordDeleted:
    def test_record_deleted_marks_the_row_deleted_and_clears_its_lock(self, tmp_path):
        f1 = _touch(tmp_path, "a.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "outcome": "", "is_locked": 1},
        ])

        with ManifestRepository().write_txn(str(manifest)) as tx:
            tx.record_deleted(str(f1))

        assert _read_row(manifest, str(f1))[:3] == ("deleted", 1, 0)
