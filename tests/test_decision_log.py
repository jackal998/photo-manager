"""WP-D — the append-only decision log (docs/behaviour/rules.md R-LOG-1).

Real SQLite and real services throughout. The one stub is the documented
``refuse_unconfirmed`` no-op from ``tests/test_outcome_chokepoint.py``, used
where a test needs the guarded UPDATE to run on a row the refusal would have
stopped.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.app_service.action_service import apply_best_copy, bulk_decide
from core.app_service.execute_service import (
    execute_decisions,
    prune_singletons,
    remove_from_review,
    restore_to_review,
)
from core.app_service.lock_gate import LockedRowsError
from core.app_service.review_service import set_decisions, set_locks
from infrastructure.manifest_repository import (
    DecisionWrite,
    ManifestRepository,
    ManifestTxn,
)
from scanner.dedup import ManifestRow
from scanner.manifest import _DDL as _MANIFEST_DDL
from scanner.manifest import write_manifest


def _file(tmp_path: Path, name: str) -> Path:
    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    path = files_dir / name
    path.write_bytes(b"x" * 64)
    return path


def _row(
    path: Path, group: str | None, action: str = "EXACT", score: float | None = None
) -> ManifestRow:
    return ManifestRow(
        source_path=str(path),
        source_label="src",
        action=action,
        source_hash=f"h-{path.name}",
        phash=None,
        hamming_distance=0,
        duplicate_of=None,
        reason="",
        group_id=group,
        score=score,
    )


def _scan(tmp_path: Path, rows: list[ManifestRow], **kwargs) -> Path:
    """A manifest from the production scan writer, migrated as load() would."""
    manifest = tmp_path / "manifest.sqlite"
    write_manifest(rows, manifest, **kwargs)
    ManifestRepository().ensure_schema(str(manifest))
    return manifest


def _manifest(tmp_path: Path, rows: list[dict], *, migrate: bool = True) -> Path:
    """A manifest in the pre-log shape (scanner DDL only), then raw rows."""
    manifest = tmp_path / "manifest.sqlite"
    conn = sqlite3.connect(str(manifest))
    try:
        conn.executescript(_MANIFEST_DDL)
        conn.commit()
    finally:
        conn.close()
    if migrate:
        ManifestRepository().ensure_schema(str(manifest))
    conn = sqlite3.connect(str(manifest))
    try:
        for r in rows:
            row = {"source_label": "src", "action": "", **r}
            cols = list(row)
            placeholders = ", ".join(f":{c}" for c in cols)
            conn.execute(
                f"INSERT INTO migration_manifest ({', '.join(cols)}) VALUES ({placeholders})",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _exec(manifest: Path, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(str(manifest))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _state(manifest: Path, path: Path) -> tuple:
    conn = sqlite3.connect(str(manifest))
    try:
        return conn.execute(
            "SELECT user_decision, is_locked, outcome FROM migration_manifest "
            "WHERE source_path = ? ORDER BY id LIMIT 1",
            (str(path),),
        ).fetchone()
    finally:
        conn.close()


def _log_rows(manifest: Path) -> list[sqlite3.Row]:
    """Every log row, oldest first, read straight off the table."""
    conn = sqlite3.connect(str(manifest))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM decision_log ORDER BY id").fetchall()
    finally:
        conn.close()


def _log(manifest: Path, **kwargs):
    return ManifestRepository().decision_log(str(manifest), **kwargs)


class TestSchema:
    def test_first_write_txn_on_pre_log_manifest_creates_table_and_logs(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _manifest(
            tmp_path, [{"source_path": str(f1), "group_id": "g1"}], migrate=False
        )
        conn = sqlite3.connect(str(manifest))
        has_log = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'decision_log'"
        ).fetchone()
        conn.close()
        assert has_log is None  # the pre-change shape has no log table

        set_decisions(str(manifest), {str(f1): "delete"})

        rows = _log_rows(manifest)
        assert len(rows) == 1
        assert rows[0]["new_decision"] == "delete"

    def test_update_or_delete_of_a_log_row_is_refused(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _manifest(tmp_path, [{"source_path": str(f1), "group_id": "g1"}])
        set_decisions(str(manifest), {str(f1): "delete"})

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _exec(manifest, "UPDATE decision_log SET new_decision = 'x'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _exec(manifest, "DELETE FROM decision_log")
        assert len(_log_rows(manifest)) == 1


class TestDecisionWrites:
    def test_set_decision_logs_old_new_verb_and_source(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])

        set_decisions(str(manifest), {str(f1): "delete"})

        (entry,) = _log(manifest)
        assert (entry.old_decision, entry.new_decision) == ("", "delete")
        assert entry.verb == "set_decision"
        assert entry.source == "user"
        assert entry.source_hash == f"h-{f1.name}"
        assert entry.group_id == "g1"
        assert entry.source_path == str(f1)

    def test_refused_locked_write_leaves_no_log_row(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _manifest(
            tmp_path, [{"source_path": str(f1), "group_id": "g1", "is_locked": 1}]
        )

        with pytest.raises(LockedRowsError):
            set_decisions(str(manifest), {str(f1): "delete"})

        assert _log_rows(manifest) == []
        assert _state(manifest, f1) == ("", 1, "")

    def test_force_unlock_logs_confirmed_and_lock_transition(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _manifest(
            tmp_path, [{"source_path": str(f1), "group_id": "g1", "is_locked": 1}]
        )

        set_decisions(str(manifest), {str(f1): "delete"}, confirmed_locked=[str(f1)])

        (entry,) = _log(manifest)
        assert entry.confirmed is True
        assert (entry.old_locked, entry.new_locked) == (True, False)
        assert _state(manifest, f1) == ("delete", 0, "")

    def test_guarded_update_skip_logs_unlocked_peer_only(self, tmp_path, monkeypatch):
        locked, peer = _file(tmp_path, "a.jpg"), _file(tmp_path, "b.jpg")
        manifest = _manifest(tmp_path, [
            {"source_path": str(locked), "group_id": "g1", "is_locked": 1},
            {"source_path": str(peer), "group_id": "g1"},
        ])
        # The refusal is off, so the guarded SQL alone decides what is written.
        monkeypatch.setattr(ManifestTxn, "refuse_unconfirmed", lambda *a, **k: None)

        with ManifestRepository().write_txn(str(manifest), verb="set_decision") as tx:
            tx.write_decisions(
                [DecisionWrite(path=str(locked), decision="delete"),
                 DecisionWrite(path=str(peer), decision="delete")],
                confirmed=(),
            )

        (entry,) = _log(manifest)
        assert entry.source_path == str(peer)
        assert _state(manifest, locked) == ("", 1, "")

    def test_rewriting_the_same_decision_logs_nothing(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])

        set_decisions(str(manifest), {str(f1): "delete"})
        set_decisions(str(manifest), {str(f1): "delete"})

        assert len(_log(manifest)) == 1

    def test_two_rows_sharing_a_path_are_logged_per_id(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1"},
            {"source_path": str(f1), "group_id": "g2"},
        ])

        set_decisions(str(manifest), {str(f1): "delete"})

        entries = _log(manifest)
        assert len(entries) == 2
        assert len({e.row_id for e in entries}) == 2

    def test_apply_best_copy_skip_locked_logs_only_written_rows(self, tmp_path):
        keeper, peer = _file(tmp_path, "keep.jpg"), _file(tmp_path, "peer.jpg")
        manifest = _scan(tmp_path, [
            _row(keeper, "g1", score=0.9),
            _row(peer, "g1", score=0.5),
        ])
        _exec(manifest, "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?", (str(peer),))

        apply_best_copy(str(manifest), 1, skip_locked=True)

        entries = _log(manifest)
        assert [e.source_path for e in entries] == [str(keeper)]
        assert entries[0].verb == "apply_best_copy"
        assert entries[0].new_locked is True


class TestOutcomeVerbs:
    @pytest.mark.parametrize(
        ("verb", "old_outcome", "new_outcome"),
        [
            ("skip_now", "", "ignored"),
            ("restore", "ignored", ""),
            ("prune", "", "ignored"),
            ("execute_skip", "", "ignored"),
            ("execute_delete", "", "deleted"),
        ],
    )
    def test_each_outcome_verb_logs_one_row_with_its_verb(
        self, tmp_path, verb, old_outcome, new_outcome
    ):
        f1, f2 = _file(tmp_path, "a.jpg"), _file(tmp_path, "b.jpg")
        if verb == "prune":
            # a is a singleton: its partner is already actioned away.
            manifest = _manifest(tmp_path, [
                {"source_path": str(f1), "group_id": "g1"},
                {"source_path": str(f2), "group_id": "g1", "action": "REVIEW_DUPLICATE",
                 "outcome": "deleted", "user_decision": "delete", "executed": 1},
            ])
        else:
            manifest = _manifest(tmp_path, [
                {"source_path": str(f1), "group_id": "g1", "outcome": old_outcome},
                {"source_path": str(f2), "group_id": "g1", "action": "REVIEW_DUPLICATE"},
            ])
        if verb in ("execute_skip", "execute_delete"):
            decision = "ignore" if verb == "execute_skip" else "delete"
            _exec(manifest, "UPDATE migration_manifest SET user_decision = ? WHERE source_path = ?",
                  (decision, str(f1)))

        if verb == "skip_now":
            remove_from_review(str(manifest), [str(f1)])
        elif verb == "restore":
            restore_to_review(str(manifest), [str(f1)])
        elif verb == "prune":
            prune_singletons(str(manifest))
        else:
            execute_decisions(str(manifest), recycle=False)

        (entry,) = _log(manifest)
        assert entry.verb == verb
        assert (entry.old_outcome, entry.new_outcome) == (old_outcome, new_outcome)

    def test_failing_log_insert_rolls_back_the_outcome_write(self, tmp_path):
        f1, f2 = _file(tmp_path, "a.jpg"), _file(tmp_path, "b.jpg")
        manifest = _manifest(tmp_path, [
            {"source_path": str(f1), "group_id": "g1", "is_locked": 1},
            {"source_path": str(f2), "group_id": "g1", "action": "REVIEW_DUPLICATE"},
        ])
        ManifestRepository().ensure_schema(str(manifest))
        _exec(manifest, "CREATE TRIGGER fail_log BEFORE INSERT ON decision_log "
                        "BEGIN SELECT RAISE(ABORT, 'log insert refused'); END")

        with pytest.raises(sqlite3.IntegrityError, match="log insert refused"):
            remove_from_review(str(manifest), [str(f1)], confirmed_locked=[str(f1)])

        assert _state(manifest, f1) == ("", 1, "")  # outcome and lock both kept
        assert _log_rows(manifest) == []


class TestLockToggle:
    def test_set_locks_logs_lock_only_change(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])

        set_locks(str(manifest), {str(f1): True})

        (entry,) = _log(manifest)
        assert entry.verb == "lock_toggle"
        assert (entry.old_decision, entry.new_decision) == ("", "")
        assert (entry.old_locked, entry.new_locked) == (False, True)

    def test_bulk_decide_lock_and_decision_use_their_own_verbs(self, tmp_path):
        # A group needs two members: the review load drops singletons.
        f1, f2 = _file(tmp_path, "a.jpg"), _file(tmp_path, "b.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1"), _row(f2, "g1", action="REVIEW_DUPLICATE")])

        bulk_decide(str(manifest), "File Name", "^a\\.jpg$", "delete")
        bulk_decide(str(manifest), "File Name", "^a\\.jpg$", "__lock__")

        # Newest first: the lock was written second.
        assert [e.verb for e in _log(manifest)] == ["bulk_lock", "bulk_decide"]


class TestScanAutoSelect:
    def test_write_manifest_logs_keepers_and_aggressive_deletes(self, tmp_path):
        keeper, loser = _file(tmp_path, "keep.jpg"), _file(tmp_path, "loser.jpg")
        manifest = _scan(
            tmp_path,
            [_row(keeper, "g1", score=0.9), _row(loser, "g1", score=0.5)],
            keepers={str(keeper)},
            non_keepers_for_delete={str(loser)},
        )

        entries = _log(manifest)
        by_path = {e.source_path: e for e in entries}
        assert set(by_path) == {str(keeper), str(loser)}
        assert {e.source for e in entries} == {"auto_select"}
        assert {e.verb for e in entries} == {"scan_auto_select"}
        assert (by_path[str(keeper)].old_locked, by_path[str(keeper)].new_locked) == (False, True)
        assert by_path[str(loser)].new_decision == "delete"

    def test_write_manifest_without_keepers_logs_nothing(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])

        assert _log(manifest) == []
        assert _log_rows(manifest) == []


class TestRescan:
    def test_rescan_to_same_output_carries_previous_log_rows(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])
        set_decisions(str(manifest), {str(f1): "delete"})
        (before,) = _log(manifest)

        _scan(tmp_path, [_row(f1, "g1")])

        (after,) = _log(manifest)
        assert (after.ts, after.verb, after.new_decision) == (
            before.ts, "set_decision", "delete",
        )
        assert after.id == before.id
        assert _state(manifest, f1) == ("", 0, "")  # the decision itself is rescanned away


class TestReconcile:
    def test_skip_directory_dismissal_logs_system_rows_but_not_locked_groups(self, tmp_path):
        recycle = tmp_path / "$RECYCLE.BIN"
        free_skip = recycle / "trashed.jpg"
        locked_skip = recycle / "kept_by_lock.jpg"
        normal_free = _file(tmp_path, "m.jpg")
        normal_locked = _file(tmp_path, "n.jpg")
        manifest = _scan(tmp_path, [
            _row(free_skip, "g_free"), _row(normal_free, "g_free"),
            _row(locked_skip, "g_locked"), _row(normal_locked, "g_locked"),
        ])
        _exec(manifest, "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
              (str(locked_skip),))

        assert ManifestRepository().reconcile_skip_directory_rows(str(manifest)) == 1

        (entry,) = _log(manifest)
        assert entry.source == "system"
        assert entry.verb == "reconcile_skip_directory"
        assert entry.source_path == str(free_skip)
        assert _state(manifest, locked_skip) == ("", 1, "")


class TestDropMove:
    def test_log_survives_dest_path_rebuild(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _manifest(tmp_path, [{"source_path": str(f1), "group_id": "g1"}])
        set_decisions(str(manifest), {str(f1): "delete"})
        (before,) = _log(manifest)

        _exec(manifest, "ALTER TABLE migration_manifest ADD COLUMN dest_path TEXT")
        ManifestRepository().ensure_schema(str(manifest))  # runs the copy-table rebuild

        conn = sqlite3.connect(str(manifest))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(migration_manifest)")}
        conn.close()
        assert "dest_path" not in columns
        assert _log(manifest) == [before]


class TestReadApi:
    def test_filters_by_path_newest_first_and_honours_limit(self, tmp_path):
        f1, f2 = _file(tmp_path, "a.jpg"), _file(tmp_path, "b.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1"), _row(f2, "g2")])
        set_decisions(str(manifest), {str(f1): "delete"})
        set_decisions(str(manifest), {str(f1): ""})
        set_decisions(str(manifest), {str(f2): "delete"})

        for_f1 = _log(manifest, source_path=str(f1))
        assert [e.new_decision for e in for_f1] == ["", "delete"]
        assert for_f1[0].id > for_f1[1].id

        assert len(_log(manifest, limit=2)) == 2
        assert len(_log(manifest)) == 3

    def test_limit_outside_1_to_10000_is_refused(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])
        for bad in (0, 10001):
            with pytest.raises(ValueError, match="limit"):
                _log(manifest, limit=bad)


class TestVerbs:
    def test_write_txn_rejects_unknown_verb(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])
        with pytest.raises(ValueError, match="unknown decision_log verb"):
            with ManifestRepository().write_txn(str(manifest), verb="not_a_verb"):
                pass
        assert _log_rows(manifest) == []

    def test_write_txn_requires_a_verb(self, tmp_path):
        f1 = _file(tmp_path, "a.jpg")
        manifest = _scan(tmp_path, [_row(f1, "g1")])
        with pytest.raises(TypeError):
            with ManifestRepository().write_txn(str(manifest)):  # type: ignore[call-arg]
                pass
