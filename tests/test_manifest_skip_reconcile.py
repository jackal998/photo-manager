"""#821 — rows left under a recycle bin / system folder by a pre-#482 scan.

The walker has refused to index anything under ``$RECYCLE.BIN`` /
``System Volume Information`` / ``.Trashes`` / ``#recycle`` since #482, but
that only stops NEW rows. A manifest built before the skip landed keeps its
recycle-bin rows live — the user's own manifest holds 3,952 of them under
``J:\\圖片\\$RECYCLE.BIN``, 2,449 of those inside a group. They are scored,
groupable, selectable as a group's keeper, and offered as delete targets
that ``send2trash`` then refuses (WinError -2147024809).

``ManifestRepository.reconcile_skip_directory_rows`` dismisses them
(``outcome='ignored'``) when the manifest is opened. These tests cover the
three things that can actually go wrong:

  a. the stale row is still in the review set after an open (the bug);
  b. a path that merely *looks* like a skip directory is dismissed too
     (the expensive false positive — it silently removes real photos);
  c. the unrelated "file no longer exists on disk" case changes behaviour.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
from pathlib import Path

from infrastructure.manifest_repository import ManifestRepository


def _write_manifest(db: Path, paths: list[tuple[str, str]]) -> None:
    """Build a manifest through the production write path.

    ``paths`` is a list of ``(source_path, group_id)``. ``write_manifest``
    emits the scanner DDL, which has NO ``outcome`` column — exactly the
    pre-#584 / pre-#482 shape this ticket is about; the column is added by
    the load-time migration.
    """
    from scanner.dedup import ManifestRow
    from scanner.manifest import write_manifest

    rows = [
        ManifestRow(
            source_path=p, source_label="src", action="EXACT",
            source_hash=f"h{i}", phash=None, hamming_distance=0,
            duplicate_of=None, reason="", group_id=gid, score=0.5,
        )
        for i, (p, gid) in enumerate(paths)
    ]
    write_manifest(rows, db)


def _outcomes(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db))
    try:
        return {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT source_path, outcome FROM migration_manifest"
            )
        }
    finally:
        conn.close()


class TestSkipDirectoryReconcile:
    """Open-time reconcile of rows under a walker skip directory."""

    def test_recycle_bin_row_leaves_the_review_set_and_is_marked_ignored(
        self, tmp_path
    ):
        """The bug itself: a pre-#482 ``$RECYCLE.BIN`` row sits in a group
        with two real photos, so it is offered for review and can win the
        group's keeper slot. After an open it must be gone from the loaded
        records AND persisted as ``outcome='ignored'`` — persisted, so the
        rest of the app (execute dialog, singleton prune, rescore) stops
        seeing it too, not just this one load.

        Fails on base: the row loads like any other.
        """
        keep_a = tmp_path / "photo_a.jpg"
        keep_b = tmp_path / "photo_b.jpg"
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        trashed = recycle_dir / "$R0KXSFA.jpg"
        for f in (keep_a, keep_b, trashed):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        gid = "g1"
        _write_manifest(
            db,
            [(str(keep_a), gid), (str(keep_b), gid), (str(trashed), gid)],
        )

        loaded = {r.file_path for r in ManifestRepository().load(str(db))}

        assert str(trashed) not in loaded, (
            "recycle-bin row is still live in the review set"
        )
        assert loaded == {str(keep_a), str(keep_b)}, (
            "the surviving group members must be unaffected"
        )
        outcomes = _outcomes(db)
        assert outcomes[str(trashed)] == "ignored"
        assert outcomes[str(keep_a)] == ""
        assert outcomes[str(keep_b)] == ""

    def test_synology_share_recycle_bin_is_covered(self, tmp_path):
        """``#recycle`` is DSM's per-share recycle bin — the folder #821
        names, and the one the walker did not actually skip before this
        change (``$RECYCLE.BIN`` was #482's target). A manifest holding one
        must reconcile it like any other skip directory.
        """
        keep = tmp_path / "photo.jpg"
        recycle_dir = tmp_path / "#recycle"
        recycle_dir.mkdir()
        trashed = recycle_dir / "deleted.jpg"
        keep.write_bytes(b"")
        trashed.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(keep), "g1"), (str(trashed), "g1")])

        ManifestRepository().reconcile_skip_directory_rows(str(db))

        outcomes = _outcomes(db)
        assert outcomes[str(trashed)] == "ignored"
        assert outcomes[str(keep)] == ""

    def test_lookalike_paths_are_not_dismissed(self, tmp_path):
        """False-positive guard — the expensive direction.

        The SQL pre-filter is a loose substring match, so all three of
        these reach the exact rule: a folder whose name merely CONTAINS a
        skip name, a file NAMED like a skip directory, and an ordinary
        photo. Dismissing any of them silently deletes real photos from
        the user's review set with no UI trace.
        """
        lookalike_dir = tmp_path / "$RECYCLE.BIN backup 2019"
        lookalike_dir.mkdir()
        in_lookalike = lookalike_dir / "photo_a.jpg"
        named_like_skip = tmp_path / "#recycle.jpg"
        ordinary = tmp_path / "photo_b.jpg"
        for f in (in_lookalike, named_like_skip, ordinary):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        gid = "g1"
        _write_manifest(
            db,
            [
                (str(in_lookalike), gid),
                (str(named_like_skip), gid),
                (str(ordinary), gid),
            ],
        )

        dismissed = ManifestRepository().reconcile_skip_directory_rows(str(db))
        loaded = {r.file_path for r in ManifestRepository().load(str(db))}

        assert dismissed == 0
        assert loaded == {
            str(in_lookalike), str(named_like_skip), str(ordinary)
        }
        assert set(_outcomes(db).values()) == {""}

    def test_missing_file_row_still_loads_unchanged(self, tmp_path):
        """Pin the neighbouring behaviour this change must NOT shift.

        A row whose file no longer exists on disk is deliberately still
        loaded — ``_photo_record`` dropped its existence check, and missing
        files are handled at execute time instead. The reconcile keys on
        the path STRING and must not start stat-ing rows (the paths live on
        a NAS; a stat per row is a per-row network round-trip). Passes on
        base and after — that is the point.
        """
        gone_a = tmp_path / "gone_a.jpg"
        gone_b = tmp_path / "gone_b.jpg"  # never created on disk

        db = tmp_path / "m.sqlite"
        gid = "g1"
        _write_manifest(db, [(str(gone_a), gid), (str(gone_b), gid)])

        loaded = {r.file_path for r in ManifestRepository().load(str(db))}

        assert loaded == {str(gone_a), str(gone_b)}
        assert set(_outcomes(db).values()) == {""}

    def test_reconcile_is_idempotent_and_reports_its_count(self, tmp_path):
        """Second open must find nothing to do.

        The reconcile runs on EVERY manifest open, so a non-idempotent
        version would re-dismiss (and re-log) forever, and — worse — could
        re-dismiss a row the user had deliberately restored. Only rows with
        ``outcome=''`` are considered, so the second call returns 0.
        """
        recycle_dir = tmp_path / ".Trashes"
        recycle_dir.mkdir()
        trashed = recycle_dir / "a.jpg"
        keep = tmp_path / "b.jpg"
        trashed.write_bytes(b"")
        keep.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(trashed), "g1"), (str(keep), "g1")])

        repo = ManifestRepository()
        assert repo.reconcile_skip_directory_rows(str(db)) == 1
        assert repo.reconcile_skip_directory_rows(str(db)) == 0

    def test_deleted_rows_are_not_reopened_as_ignored(self, tmp_path):
        """A recycle-bin row already finalised as ``deleted`` keeps that
        outcome. ``outcome`` is write-once post-execute state (#584); a
        reconcile that overwrote ``deleted`` with ``ignored`` would lose
        the record that the file was actually trashed, and flip
        ``executed`` back to 0.
        """
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        trashed = recycle_dir / "a.jpg"
        keep = tmp_path / "b.jpg"
        trashed.write_bytes(b"")
        keep.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(trashed), "g1"), (str(keep), "g1")])

        repo = ManifestRepository()
        repo.finalize_outcome(str(db), [str(trashed)], "deleted")
        assert repo.reconcile_skip_directory_rows(str(db)) == 0
        assert _outcomes(db)[str(trashed)] == "deleted"

    def test_read_only_manifest_still_opens(self, tmp_path, caplog):
        """A manifest the process cannot write to must still LOAD.

        The reconcile is housekeeping the user never asked for, running
        inside someone else's ``load()``. Before the fail-soft guard the
        write raised ``sqlite3.OperationalError: attempt to write a
        readonly database`` straight out of ``load()`` — which
        ``app/web/routes/review.py:58-62`` turns into an HTTP 422 and which
        aborts the Qt load worker. A read-only manifest opened fine on
        base; making it unopenable would be a regression caused purely by
        the cleanup.

        Contract: the rows stay live for that open (exactly the pre-#821
        behaviour), and a WARNING names the manifest.
        """
        from loguru import logger

        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        trashed = recycle_dir / "a.jpg"
        keep = tmp_path / "b.jpg"
        trashed.write_bytes(b"")
        keep.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(trashed), "g1"), (str(keep), "g1")])
        # Migrate BEFORE freezing the file: ensure_schema's ALTERs are a
        # pre-existing write on the load() path, not the behaviour under
        # test here. Without this the test would be pinning the schema
        # migration's failure mode instead of the reconcile's.
        ManifestRepository().ensure_schema(str(db))

        os.chmod(db, stat.S_IREAD)
        handler_id = logger.add(
            caplog.handler, format="{message}", level="WARNING"
        )
        try:
            with caplog.at_level(logging.WARNING):
                loaded = {
                    r.file_path for r in ManifestRepository().load(str(db))
                }
        finally:
            logger.remove(handler_id)
            os.chmod(db, stat.S_IWRITE | stat.S_IREAD)

        assert loaded == {str(trashed), str(keep)}, (
            "a read-only manifest must still open, with its rows intact"
        )
        assert _outcomes(db)[str(trashed)] == "", (
            "nothing was dismissed — the DB could not be written"
        )
        warnings = [
            r.message for r in caplog.records
            if "Could not dismiss" in r.message
        ]
        assert len(warnings) == 1, (
            f"expected one WARNING naming the failure, got: "
            f"{[r.message for r in caplog.records]}"
        )
        assert str(db) in warnings[0]

    def test_row_finalised_deleted_after_the_select_is_not_reopened(
        self, tmp_path
    ):
        """The candidates are read on a connection that is then CLOSED, so
        there is a window before the dismissal write.

        If an execute batch finalises one of those candidates as
        ``deleted`` (``executed=1``) inside that window — reachable in the
        web threadpool, where ``GET /api/manifest`` and the execute route
        run concurrently — an unguarded write would overwrite it with
        ``ignored`` and flip ``executed`` back to 0, defeating serially
        what ``test_deleted_rows_are_not_reopened_as_ignored`` pins.

        This encodes that window without threads: take the candidate list,
        finalise one of them out of band, then run the write step with the
        now-stale list.
        """
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        raced = recycle_dir / "raced.jpg"
        other = recycle_dir / "other.jpg"
        keep = tmp_path / "keep.jpg"
        for f in (raced, other, keep):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(
            db,
            [(str(raced), "g1"), (str(other), "g1"), (str(keep), "g1")],
        )

        repo = ManifestRepository()
        repo.ensure_schema(str(db))
        conn = sqlite3.connect(str(db))
        try:
            ids = {
                row[1]: row[0]
                for row in conn.execute(
                    "SELECT id, source_path FROM migration_manifest"
                )
            }
        finally:
            conn.close()
        candidates = [ids[str(raced)], ids[str(other)]]   # what the SELECT saw

        # …the window: an execute batch trashes one of them for real.
        repo.finalize_outcome(str(db), [str(raced)], "deleted")

        dismissed = repo._dismiss_skipped_rows(str(db), candidates)

        assert dismissed == 1, (
            "only the still-in-review candidate may be dismissed"
        )
        conn = sqlite3.connect(str(db))
        try:
            state = {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "SELECT source_path, outcome, executed "
                    "FROM migration_manifest"
                )
            }
        finally:
            conn.close()
        assert state[str(raced)] == ("deleted", 1), (
            "the real deletion was overwritten by the stale reconcile"
        )
        assert state[str(other)] == ("ignored", 0)
        assert state[str(keep)] == ("", 0)

    def test_locked_row_under_skip_directory_stays_in_review_and_locked(
        self, tmp_path
    ):
        """#982 — a lock means "don't touch this row". The open-time reconcile
        is a write with no user action behind it, so it must leave a locked
        recycle-bin row alone: ``outcome=''``, still locked, still loaded for
        review. An unlocked recycle-bin row in a DIFFERENT group, with no
        lock anywhere in it, is dismissed as before. (#982 M1: a partner in
        the SAME group as the lock is kept too, see the group tests above.)
        """
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        locked_trashed = recycle_dir / "locked.jpg"
        unlocked_trashed = recycle_dir / "unlocked.jpg"
        keep = tmp_path / "keep.jpg"
        keep_other = tmp_path / "keep_other.jpg"
        for f in (locked_trashed, unlocked_trashed, keep, keep_other):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(
            db,
            [
                (str(locked_trashed), "g1"),
                (str(keep), "g1"),
                (str(unlocked_trashed), "g2"),
                (str(keep_other), "g2"),
            ],
        )
        repo = ManifestRepository()
        repo.ensure_schema(str(db))
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
                (str(locked_trashed),),
            )
            conn.commit()
        finally:
            conn.close()

        loaded = {r.file_path for r in repo.load(str(db))}

        assert str(locked_trashed) in loaded, (
            "a locked row must stay in the review set after an open"
        )
        assert str(unlocked_trashed) not in loaded
        outcomes = _outcomes(db)
        assert outcomes[str(locked_trashed)] == "", (
            "the reconcile dismissed a locked row"
        )
        assert outcomes[str(unlocked_trashed)] == "ignored"
        assert outcomes[str(keep)] == ""
        assert outcomes[str(keep_other)] == ""
        conn = sqlite3.connect(str(db))
        try:
            (is_locked,) = conn.execute(
                "SELECT is_locked FROM migration_manifest WHERE source_path = ?",
                (str(locked_trashed),),
            ).fetchone()
        finally:
            conn.close()
        assert is_locked == 1, "the reconcile cleared a lock"

    def test_reconcile_reports_how_many_locked_rows_it_left(self, tmp_path):
        """#982 — the open reports the locked rows it left in place.

        The dismissed count is the return value (unchanged, 1 here); the
        locked-left count surfaces on the existing app-log line for this
        pass, so the one count a reader needs is printed next to the other.
        """
        from loguru import logger

        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        locked_trashed = recycle_dir / "locked.jpg"
        unlocked_trashed = recycle_dir / "unlocked.jpg"
        keep = tmp_path / "keep.jpg"
        keep_other = tmp_path / "keep_other.jpg"
        for f in (locked_trashed, unlocked_trashed, keep, keep_other):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(
            db,
            [
                (str(locked_trashed), "g1"),
                (str(keep), "g1"),
                (str(unlocked_trashed), "g2"),
                (str(keep_other), "g2"),
            ],
        )
        repo = ManifestRepository()
        repo.ensure_schema(str(db))
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
                (str(locked_trashed),),
            )
            conn.commit()
        finally:
            conn.close()

        messages: list[str] = []
        handler_id = logger.add(
            lambda m: messages.append(m.record["message"]), level="INFO"
        )
        try:
            dismissed = repo.reconcile_skip_directory_rows(str(db))
        finally:
            logger.remove(handler_id)

        assert dismissed == 1
        assert any(
            "Left 1 skip-directory row(s)" in msg for msg in messages
        ), f"no locked-left count logged, got: {messages}"

    def test_lock_set_after_the_candidate_read_is_not_dismissed(self, tmp_path):
        """#982 — the same race as the outcome guard, for the lock.

        The candidates are read (unlocked) on a closed connection; a user
        may lock one of them before the dismissal write. The write re-checks
        ``is_locked = 0`` inside the statement, so it leaves that row alone.
        """
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        raced = recycle_dir / "raced.jpg"
        keep = tmp_path / "keep.jpg"
        for f in (raced, keep):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(raced), "g1"), (str(keep), "g1")])
        repo = ManifestRepository()
        repo.ensure_schema(str(db))
        conn = sqlite3.connect(str(db))
        try:
            (raced_id,) = conn.execute(
                "SELECT id FROM migration_manifest WHERE source_path = ?",
                (str(raced),),
            ).fetchone()
        finally:
            conn.close()

        # …the window: the user locks the row after the SELECT.
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "UPDATE migration_manifest SET is_locked = 1 WHERE id = ?",
                (raced_id,),
            )
            conn.commit()
        finally:
            conn.close()

        dismissed = repo._dismiss_skipped_rows(str(db), [raced_id])

        assert dismissed == 0
        conn = sqlite3.connect(str(db))
        try:
            state = conn.execute(
                "SELECT outcome, is_locked FROM migration_manifest WHERE id = ?",
                (raced_id,),
            ).fetchone()
        finally:
            conn.close()
        assert state == ("", 1), "a locked row was dismissed by the reconcile"

    def test_locked_skip_row_keeps_its_unlocked_group_partner_visible(
        self, tmp_path
    ):
        """#982 M1 (a) — the group is the unit, not the row.

        The group holds a locked recycle-bin row and an unlocked one. Dismissing
        the unlocked row would leave one member, and the load's orphan-skip
        would hide the group: the locked row would vanish from review with no
        user action. So the whole group stays, and both rows load.
        """
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        locked_trashed = recycle_dir / "a.jpg"
        unlocked_trashed = recycle_dir / "b.jpg"
        for f in (locked_trashed, unlocked_trashed):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(
            db, [(str(locked_trashed), "g1"), (str(unlocked_trashed), "g1")]
        )
        _set_locked(db, str(locked_trashed))

        loaded = {r.file_path for r in ManifestRepository().load(str(db))}

        assert loaded == {str(locked_trashed), str(unlocked_trashed)}
        assert set(_outcomes(db).values()) == {""}
        assert _is_locked(db, str(locked_trashed)) == 1

    def test_locked_non_skip_partner_keeps_skip_row_visible(self, tmp_path):
        """#982 M1 (b) — the lock may sit on a row OUTSIDE the skip directory.

        The group's only partner of the locked photo is a recycle-bin file. The
        group contains a locked row, so the recycle-bin row is left in place and
        the group stays visible.
        """
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        locked_keep = tmp_path / "photo.jpg"
        trashed = recycle_dir / "b.jpg"
        for f in (locked_keep, trashed):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(locked_keep), "g1"), (str(trashed), "g1")])
        _set_locked(db, str(locked_keep))

        loaded = {r.file_path for r in ManifestRepository().load(str(db))}

        assert loaded == {str(locked_keep), str(trashed)}
        assert set(_outcomes(db).values()) == {""}

    def test_group_without_a_locked_row_is_dismissed_as_before(self, tmp_path):
        """#982 M1 (c) — the rule changes nothing for an unlocked group."""
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        trashed = recycle_dir / "a.jpg"
        keep = tmp_path / "b.jpg"
        for f in (trashed, keep):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(trashed), "g1"), (str(keep), "g1")])

        ManifestRepository().reconcile_skip_directory_rows(str(db))

        outcomes = _outcomes(db)
        assert outcomes[str(trashed)] == "ignored"
        assert outcomes[str(keep)] == ""

    def test_lock_on_the_partner_after_the_candidate_read_blocks_the_dismiss(
        self, tmp_path
    ):
        """#982 M1 (d) — the group-level race guard.

        The skip row is still unlocked when the candidates are read. Then the
        user locks its PARTNER (not the skip row) before the write. The write
        must re-check the whole group and leave the skip row in review.
        """
        recycle_dir = tmp_path / "$RECYCLE.BIN"
        recycle_dir.mkdir()
        trashed = recycle_dir / "a.jpg"
        partner = tmp_path / "b.jpg"
        for f in (trashed, partner):
            f.write_bytes(b"")

        db = tmp_path / "m.sqlite"
        _write_manifest(db, [(str(trashed), "g1"), (str(partner), "g1")])
        repo = ManifestRepository()
        repo.ensure_schema(str(db))
        conn = sqlite3.connect(str(db))
        try:
            (trashed_id,) = conn.execute(
                "SELECT id FROM migration_manifest WHERE source_path = ?",
                (str(trashed),),
            ).fetchone()
        finally:
            conn.close()

        # …the window: the partner is locked after the SELECT.
        _set_locked(db, str(partner))

        dismissed = repo._dismiss_skipped_rows(str(db), [trashed_id])

        assert dismissed == 0
        assert _outcomes(db)[str(trashed)] == ""


def _set_locked(db: Path, source_path: str) -> None:
    ManifestRepository().ensure_schema(str(db))  # adds the is_locked column
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
            (source_path,),
        )
        conn.commit()
    finally:
        conn.close()


def _is_locked(db: Path, source_path: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        (value,) = conn.execute(
            "SELECT is_locked FROM migration_manifest WHERE source_path = ?",
            (source_path,),
        ).fetchone()
    finally:
        conn.close()
    return value
