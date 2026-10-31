"""A4 — outcome writes through the guarded chokepoint (design section 5, 6, 7).

Step 1 of A4 (BE-15, BE-16) pins behaviour that exists on master today: they
must pass on the base commit and on the A4 head alike. Later steps add the
red tests for the stale-plan, SQL-guard and record_deleted acceptance items.
"""

from __future__ import annotations

import os
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

        # The per-file outcome write is ManifestTxn.record_deleted (A4).
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
        assert _read_row(manifest, str(f1))[:2] == ("", 0)  # the write never ran

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
        assert _read_row(manifest, str(f1))[0] == "ignored"  # the write never ran

    def test_prune_refuses_a_singleton_locked_after_the_plan(self, tmp_path, monkeypatch):
        lone = _touch(tmp_path, "lone.jpg")
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(lone), "group_id": "g9", "outcome": ""},
        ])
        _lock_before_write(monkeypatch, manifest, str(lone))

        with pytest.raises(ValueError) as exc:
            prune_singletons(str(manifest))

        assert exc.value.args[0] == "locked_paths"
        assert _read_row(manifest, str(lone))[:2] == ("", 0)  # the write never ran

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
        # The lock is committed on a SEPARATE connection, after the gate and
        # before the skip batch's transaction. Locking on the batch's own
        # connection would be rolled back with the refusal, which proves nothing.
        real_transition = getattr(execute_module, "_transition_outcome", None)
        if real_transition is not None:
            def _lock_then_transition(*args, **kwargs):
                _set_locked(manifest, str(ignored_row))
                return real_transition(*args, **kwargs)

            monkeypatch.setattr(
                execute_module, "_transition_outcome", _lock_then_transition
            )

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


# ---------------------------------------------------------------------------
# A9: Execute re-checks each delete row right before its file call (D6).
# The change lands on a SEPARATE connection, from inside the file-call step
# (the fake recycle below), so it happens after the gate and before the call.
# ---------------------------------------------------------------------------


class _RecycleStep:
    """Stands in for DeleteService.delete_to_recycle; runs ``before`` per file."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.before = lambda _path: None


@pytest.fixture
def recycle_step(monkeypatch):
    from core.services.interfaces import DeleteResult
    from infrastructure.delete_service import DeleteService

    step = _RecycleStep()

    def _fake_recycle(_self, paths):
        for path in paths:
            step.calls.append(path)
            step.before(path)
            os.remove(path)
        return DeleteResult(success_paths=list(paths), failed=[])

    monkeypatch.setattr(DeleteService, "delete_to_recycle", _fake_recycle)
    return step


def _set_outcome(manifest: Path, path: str, outcome: str) -> None:
    conn = sqlite3.connect(str(manifest))
    try:
        conn.execute(
            "UPDATE migration_manifest SET outcome = ? WHERE source_path = ?",
            (outcome, path),
        )
        conn.commit()
    finally:
        conn.close()


def _two_deletes(tmp_path: Path):
    """Two unlocked delete rows and a keeper; returns (manifest, a, b)."""
    row_a, row_b = _touch(tmp_path, "a.jpg"), _touch(tmp_path, "b.jpg")
    keeper = _touch(tmp_path, "k.jpg")
    manifest = _make_manifest(tmp_path, [
        {"source_path": str(row_a), "group_id": "g1", "outcome": "",
         "user_decision": "delete", "file_size_bytes": 64},
        {"source_path": str(row_b), "group_id": "g1", "outcome": "",
         "user_decision": "delete", "file_size_bytes": 64},
        {"source_path": str(keeper), "group_id": "g1", "outcome": "",
         "user_decision": "", "file_size_bytes": 64},
    ])
    return manifest, row_a, row_b


def _lock_the_others_before_first_call(recycle_step, manifest: Path, rows: list[Path]):
    """Land a change on every other delete row before the first file call returns.

    The first file call is the one that runs after the gate; every other row
    is still unexecuted at that point, so the change is always after the gate
    and before that row's own re-check. Which row goes first is left to the
    query order, and the test asserts it from ``recycle_step.calls``.
    """
    def _before(path: str) -> None:
        if len(recycle_step.calls) == 1:
            for other in rows:
                if str(other) != path:
                    _set_locked(manifest, str(other))
    recycle_step.before = _before


class TestExecuteRowRecheck:
    def test_row_locked_after_the_gate_is_not_deleted_and_keeps_its_lock(
        self, tmp_path, recycle_step
    ):
        manifest, row_a, row_b = _two_deletes(tmp_path)
        _lock_the_others_before_first_call(recycle_step, manifest, [row_a, row_b])

        result = execute_decisions(str(manifest), recycle=True)

        assert len(recycle_step.calls) == 1, "the locked row never reaches its file call"
        first = recycle_step.calls[0]
        locked = str(row_b) if first == str(row_a) else str(row_a)
        assert os.path.exists(locked), "a row locked after the gate is not deleted"
        assert _read_row(manifest, locked)[:3] == ("", 0, 1), "outcome and lock unchanged"
        assert locked in [p for p, _reason in result["failed"]]
        assert locked not in result["success_paths"]
        assert _read_row(manifest, first)[:3] == ("deleted", 1, 0), (
            "the unaffected row is still deleted and recorded"
        )

    def test_row_dismissed_after_the_gate_is_not_deleted(self, tmp_path, recycle_step):
        manifest, row_a, row_b = _two_deletes(tmp_path)

        def _dismiss_others(path: str) -> None:
            if len(recycle_step.calls) == 1:
                for other in (row_a, row_b):
                    if str(other) != path:
                        _set_outcome(manifest, str(other), "ignored")

        recycle_step.before = _dismiss_others

        result = execute_decisions(str(manifest), recycle=True)

        assert len(recycle_step.calls) == 1
        dismissed = str(row_b) if recycle_step.calls[0] == str(row_a) else str(row_a)
        assert os.path.exists(dismissed), "a row dismissed after the gate is not deleted"
        assert _read_row(manifest, dismissed)[:3] == ("ignored", 0, 0)
        assert dismissed in [p for p, _reason in result["failed"]]

    def test_row_handled_elsewhere_after_the_gate_is_not_deleted(
        self, tmp_path, recycle_step
    ):
        manifest, row_a, row_b = _two_deletes(tmp_path)

        def _handle_others(path: str) -> None:
            if len(recycle_step.calls) == 1:
                for other in (row_a, row_b):
                    if str(other) != path:
                        _set_outcome(manifest, str(other), "deleted")

        recycle_step.before = _handle_others

        result = execute_decisions(str(manifest), recycle=True)

        assert len(recycle_step.calls) == 1
        handled = str(row_b) if recycle_step.calls[0] == str(row_a) else str(row_a)
        assert os.path.exists(handled), "a row already handled is not re-deleted"
        assert _read_row(manifest, handled)[:3] == ("deleted", 0, 0)
        assert handled in [p for p, _reason in result["failed"]]

    def test_row_locked_before_and_named_is_still_deleted(self, tmp_path, recycle_step):
        manifest, row_a, row_b = _two_deletes(tmp_path)
        _set_locked(manifest, str(row_a))

        result = execute_decisions(
            str(manifest), recycle=True, confirmed_locked=[str(row_a)]
        )

        assert str(row_a) in result["success_paths"]
        assert not row_a.exists()
        assert _read_row(manifest, str(row_a))[:3] == ("deleted", 1, 0)
