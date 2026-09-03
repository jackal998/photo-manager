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

import sqlite3
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
