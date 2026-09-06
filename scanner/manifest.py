"""Write and summarise the migration manifest SQLite database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from scanner.dedup import ManifestRow

_DDL = """
CREATE TABLE IF NOT EXISTS migration_manifest (
    id               INTEGER PRIMARY KEY,
    source_path      TEXT    NOT NULL,
    source_label     TEXT    NOT NULL,
    action           TEXT    NOT NULL,
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
    mtime            TEXT,
    pixel_width      INTEGER,
    pixel_height     INTEGER,
    exif_tag_count   INTEGER,
    gps_present      INTEGER NOT NULL DEFAULT 0,
    xmp_derived      INTEGER NOT NULL DEFAULT 0,
    score            REAL,
    subsec_time_original TEXT,
    offset_time_original TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_hash ON migration_manifest(source_hash);
CREATE INDEX IF NOT EXISTS idx_phash       ON migration_manifest(phash);
CREATE INDEX IF NOT EXISTS idx_action      ON migration_manifest(action);
CREATE INDEX IF NOT EXISTS idx_group_id    ON migration_manifest(group_id);
"""

_INSERT = """
INSERT INTO migration_manifest
    (source_path, source_label, action, source_hash,
     phash, hamming_distance, group_id, reason,
     file_size_bytes, shot_date, creation_date, mtime,
     pixel_width, pixel_height,
     exif_tag_count, gps_present, xmp_derived, score,
     subsec_time_original, offset_time_original)
VALUES (?, ?, ?, ?, ?, ?, ?, ?,  ?, ?, ?, ?, ?, ?,  ?, ?, ?, ?,  ?, ?)
"""


def write_manifest(
    rows: list[ManifestRow],
    output: Path,
    *,
    keepers: "set[str] | None" = None,
    non_keepers_for_delete: "set[str] | None" = None,
) -> None:
    """Create (or overwrite) the SQLite manifest at ``output``.

    #464 — writes to a sibling ``<output>.tmp.sqlite`` first, then
    ``os.replace()`` over the destination once the connection is closed.
    Guarantees:

      * Orphan ``-wal`` / ``-shm`` sidecars from a previous writer (or a
        mid-write cancel — see #463) never bleed into the new manifest;
        the temp DB owns its own sidecars, which are checkpointed away
        when the connection closes.
      * A partial write (process killed mid-INSERT, see #460) never
        reaches the destination — the temp file may be left behind for
        cleanup on the next call, but the live manifest stays consistent.

    #651 — ``keepers`` / ``non_keepers_for_delete`` fold the post-scan
    auto-select decisions (keeper locks, optional aggressive-delete
    decisions) INTO this same write. When ``keepers is not None`` the
    schema is lazily migrated and the auto-select UPDATEs run on the
    SAME tmp connection, BEFORE the single ``conn.commit()`` and the
    atomic ``os.replace()`` below — so a crash or exception mid-write
    never leaves a manifest on disk with rows but no keeper lock (the
    incoherence window the separate post-write call used to leave
    open). ``keepers is None`` (the default) is the byte-identical
    pre-#651 code path: no migration, no auto-select UPDATEs.

    Args:
        rows: Classified manifest rows to insert.
        output: Destination path for the manifest SQLite file.
        keepers: Paths receiving ``user_decision=""`` + ``is_locked=1``.
            ``None`` skips the auto-select write entirely (no
            auto-select configured for this scan). An empty set is
            treated the same as ``None`` by the caller's convention —
            pass ``None`` when there are no keepers to write.
        non_keepers_for_delete: Paths receiving
            ``user_decision='delete'`` (the aggressive #393 path).
            Ignored when ``keepers`` is ``None``.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output.with_name(output.name + ".tmp.sqlite")
    # Sweep both:
    #   1. Stale TEMP from a previously-killed run — would block sqlite3.connect
    #      on Windows (file lock) and corrupt the new write otherwise.
    #   2. Orphan DESTINATION -wal/-shm — os.replace below only renames the
    #      .sqlite itself; if a prior writer crashed pre-checkpoint and left
    #      sidecars next to the destination, they'd survive the replace.
    #      SQLite invalidates them via salt mismatch on the next open, so they
    #      don't corrupt the manifest — but they leave junk on disk and
    #      compound debugging when "the manifest looks empty/old."
    for stale in (
        tmp_path,
        tmp_path.with_name(tmp_path.name + "-wal"),
        tmp_path.with_name(tmp_path.name + "-shm"),
        output.with_name(output.name + "-wal"),
        output.with_name(output.name + "-shm"),
    ):
        if stale.exists():
            stale.unlink()

    # ``with sqlite3.connect(...)`` only commits/rolls back on exit — it
    # does NOT close the connection. On Windows the connection keeps a
    # file lock that would fail ``os.replace`` below, so we use explicit
    # try/finally with ``conn.close()`` to release the handle.
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_DDL)
        conn.executemany(
            _INSERT,
            [
                (
                    r.source_path,
                    r.source_label,
                    r.action,
                    r.source_hash,
                    r.phash,
                    r.hamming_distance,
                    r.group_id,
                    r.reason,
                    r.file_size_bytes,
                    r.shot_date,
                    r.creation_date,
                    r.mtime,
                    r.pixel_width,
                    r.pixel_height,
                    r.exif_tag_count,
                    int(r.gps_present),
                    int(r.xmp_derived),
                    r.score,
                    r.subsec_time_original,
                    r.offset_time_original,
                )
                for r in rows
            ],
        )

        # #651 — fold the post-scan auto-select writes into this same
        # connection/transaction, BEFORE commit, so they are part of the
        # one atomic os.replace() below. keepers is None is the
        # pre-#651 no-auto-select path — byte-identical, no migration,
        # no UPDATEs.
        if keepers is not None:
            from core.services.auto_select import build_auto_select_writes
            from infrastructure.manifest_repository import (
                _UPDATE_DECISION_SQL,
                _UPDATE_LOCK_SQL,
                migrate_manifest_schema,
            )

            # write_manifest's own _DDL above does not include is_locked /
            # user_decision-is-already-present-but-lock-column-missing on
            # older schemas — migrate this tmp connection's schema first
            # so the UPDATEs below never hit a missing column.
            migrate_manifest_schema(conn)
            decisions, lock_states = build_auto_select_writes(
                keepers, non_keepers_for_delete
            )
            if decisions:
                conn.executemany(
                    _UPDATE_DECISION_SQL,
                    [(v, k) for k, v in decisions.items()],
                )
            if lock_states:
                conn.executemany(
                    _UPDATE_LOCK_SQL,
                    [(1 if v else 0, k) for k, v in lock_states.items()],
                )

        conn.commit()
        # Force WAL checkpoint so the temp DB has no live sidecars when
        # the connection closes — keeps the rename single-file atomic.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    # Connection is fully closed here. ``os.replace`` is atomic on POSIX
    # and on Windows (Python 3.3+) when the destination exists.
    os.replace(tmp_path, output)


def print_summary(rows: list[ManifestRow], skipped: int = 0) -> None:
    """Print an action-count summary table to stdout.

    Args:
        rows: Classified manifest rows (action breakdown is derived from these).
        skipped: Count of files that were walked + hashed but excluded from
            the manifest (unreadable / decode-failed). When > 0, a separate
            ``Skipped (unreadable)`` line is printed so the headline number
            reconciles with the ``Skipped N unreadable file(s):`` line that
            scan_worker / scan.py emit earlier in the log (#87).

    The headline label is ``Indexed in manifest`` — accurately describing
    ``len(rows)``, which is the manifest row count, not a "files scanned"
    count. The previous wording falsely implied 0 files were processed when
    every file was decode-skipped (#87).
    """
    from collections import Counter
    from infrastructure.i18n import t
    counts: Counter = Counter(r.action for r in rows)
    total = len(rows)

    # Internal action strings stay as dict keys (they drive sort order
    # and tree rendering); only the row labels are localised. See #242.
    action_label_keys = (
        ("KEEP", "manifest_summary.keep"),
        ("EXACT", "manifest_summary.exact"),
        ("REVIEW_DUPLICATE", "manifest_summary.review_duplicate"),
        ("UNDATED", "manifest_summary.undated"),
    )

    print("\n── Migration Manifest Summary ──────────────────────")
    print(f"  Indexed in manifest : {total:>7,}")
    if skipped:
        print(f"  Skipped (unreadable): {skipped:>7,}")
    for action, key in action_label_keys:
        label = t(key)
        n = counts.get(action, 0)
        pct = 100 * n / total if total else 0
        print(f"  {label:<26}: {n:>7,}  ({pct:.1f}%)")
    other = total - sum(counts[a] for a, _ in action_label_keys)
    if other:
        other_label = t("manifest_summary.other")
        print(f"  {other_label:<26}: {other:>7,}")
    print("────────────────────────────────────────────────────")

    n_groups = len({r.group_id for r in rows if r.group_id})
    n_grouped = sum(1 for r in rows if r.group_id)
    print(f"\n── Group Summary ───────────────────────────────────")
    print(f"  Groups (≥2 similar files) : {n_groups:>7,}")
    print(f"  Files in groups           : {n_grouped:>7,}")
    print(f"  Isolated (no match)       : {total - n_grouped:>7,}")
    print("────────────────────────────────────────────────────\n")
