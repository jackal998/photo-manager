"""ManifestRepository — loads all rows from migration_manifest.sqlite
into PhotoRecord/PhotoGroup objects for the Qt review UI.

Load flow:
  Every row is loaded except those with user_decision='removed' (dismissed by
  the user via "Remove from List").  Files with a duplicate_of reference (EXACT /
  REVIEW_DUPLICATE) are grouped with their reference as a pair.  If the reference
  file itself is marked 'removed', the inline yield is also skipped.
  All records load with is_mark=False; is_locked is read from the
  ``is_locked`` column (0 / 1, defaults to 0 for older manifests via the
  additive migration).

  EXIF date is only read for REVIEW_DUPLICATE rows (performance).

  If the DB pre-dates the user_decision column, an ALTER TABLE migration runs
  automatically so older manifests open without error.

Save flow:
  Writes rec.user_decision for every record back to the manifest. Lock
  state is written by ``batch_update_lock_state`` separately — locking is
  a UI affordance, not a per-decision side-effect.
  (executed=1 is set separately by ExecuteActionDialog after operations run.)
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from loguru import logger

from core.models import PhotoGroup, PhotoRecord
from infrastructure.utils import get_exif_datetime_original, get_filesystem_creation_datetime


def _connect(path: str) -> sqlite3.Connection:
    """Open a SQLite connection with performance pragmas.

    WAL mode is persisted in the DB file after the first write connection.
    synchronous=NORMAL is session-level (safe for local desktop use —
    survives OS crashes but not power loss mid-write, which is acceptable here).
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -32000")   # 32 MB page cache
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn

_LOAD_ALL_SQL = """
SELECT id, source_path, source_label, group_id, hamming_distance, reason,
       action, executed, user_decision, is_locked,
       file_size_bytes, shot_date, creation_date, mtime,
       pixel_width, pixel_height,
       phash,
       score
FROM   migration_manifest
WHERE  executed = 0
ORDER  BY
    group_id NULLS LAST,
    -- "Ref tier" first (KEEP / UNDATED / unset all render as "Ref"
    -- in the tree per app/views/tree_model_builder._file_similarity), then
    -- duplicates in descending similarity: EXACT (100%) before
    -- REVIEW_DUPLICATE (near-match). Top-down a group reads as "winner"
    -- → strongest match → weaker matches (#55, #76). The legacy MOVE
    -- action (and dest_path column) were dropped in #433 — old MOVE rows
    -- are migrated to '' (undecided) by the drop-move migration below,
    -- which also falls into tier 1 via the '' branch.
    CASE action
        WHEN 'KEEP'             THEN 1
        WHEN 'UNDATED'          THEN 1
        WHEN ''                 THEN 1
        WHEN 'EXACT'            THEN 2
        WHEN 'REVIEW_DUPLICATE' THEN 3
        ELSE 4
    END,
    id
"""

# Migration list: (column_name, DDL_snippet)
# Applied in order — safe to re-run (ALTER TABLE silently fails if column exists).
_MIGRATIONS = [
    ("user_decision",   "TEXT    NOT NULL DEFAULT ''"),
    ("file_size_bytes", "INTEGER"),
    ("shot_date",       "TEXT"),
    ("creation_date",   "TEXT"),
    ("mtime",           "TEXT"),
    ("group_id",        "TEXT"),
    ("pixel_width",     "INTEGER"),
    ("pixel_height",    "INTEGER"),
    ("is_locked",       "INTEGER NOT NULL DEFAULT 0"),
    # Scoring system (#187) — raw signals + composite score. Populated by
    # the extended exiftool pass (PR 2) and scorer (PR 3/4). NULL on old
    # manifests; scorer treats NULL as 0.0 so old manifests degrade gracefully.
    ("exif_tag_count",  "INTEGER"),
    ("gps_present",     "INTEGER NOT NULL DEFAULT 0"),
    ("xmp_derived",     "INTEGER NOT NULL DEFAULT 0"),
    ("score",           "REAL"),
]

# #433 — drop the legacy ``dest_path`` column and migrate ``action='MOVE'``
# rows to the canonical undecided action ('').  ``dest_path`` + the MOVE
# action were the handshake to the now-defunct external photo-transfer tool.
#
# This is a STRUCTURAL migration (column removal), not an additive ALTER —
# it cannot live in ``_MIGRATIONS`` (which is ADD-COLUMN-only and runs the
# same DDL idempotently).  SQLite < 3.35 has no ``DROP COLUMN``, so we use
# the portable copy-table dance (create new table without the column, copy
# every row, drop old, rename) inside a single transaction.  Idempotent:
# guarded by a ``PRAGMA table_info`` check so re-running on an
# already-migrated manifest is a no-op.  The MOVE→'' UPDATE is folded into
# the copy SELECT so old manifests open with the legacy rows already
# normalised to the empty action the review UI renders as a Ref-tier row.
_DROP_MOVE_COLUMN = "dest_path"

# New-schema column list (matches scanner.manifest._DDL after #433), in the
# order the rebuilt table declares them.  Used by the drop-move copy dance
# to SELECT the surviving columns from the old table.
_POST_DROP_COLUMNS = (
    "id", "source_path", "source_label", "action", "source_hash",
    "phash", "hamming_distance", "group_id", "reason",
    "executed", "user_decision",
    "file_size_bytes", "shot_date", "creation_date", "mtime",
    "pixel_width", "pixel_height",
    "exif_tag_count", "gps_present", "xmp_derived", "score",
)

_UPDATE_DECISION_SQL = """
UPDATE migration_manifest SET user_decision = ? WHERE source_path = ?
"""

_UPDATE_LOCK_SQL = """
UPDATE migration_manifest SET is_locked = ? WHERE source_path = ?
"""

_MARK_EXECUTED_SQL = """
UPDATE migration_manifest SET executed = 1 WHERE source_path = ?
"""

_REMOVE_FROM_REVIEW_SQL = """
UPDATE migration_manifest SET user_decision = 'removed' WHERE source_path = ?
"""


def _photo_record(
    source_path: str,
    group_number: int,
    is_mark: bool,
    is_locked: bool,
    action: str = "",
    read_exif: bool = True,
    user_decision: str = "",
    db_file_size: "int | None" = None,
    db_shot_date: "str | None" = None,
    db_creation_date: "str | None" = None,
    db_mtime: "str | None" = None,
    hamming_distance: "int | None" = None,
    db_pixel_width: "int | None" = None,
    db_pixel_height: "int | None" = None,
    db_phash: "str | None" = None,
    db_score: "float | None" = None,
) -> PhotoRecord:
    """Build a PhotoRecord, preferring cached DB metadata over filesystem reads.

    When the four db_* parameters are populated (new manifests), no filesystem
    stat calls are made — load time drops from minutes to milliseconds on NAS.
    When they are None (old manifests), falls back to the original filesystem
    reads so older manifests still work without re-scanning.

    The file-existence check has been removed; missing files are handled at
    execute time instead (ExecuteActionDialog._delete_file).
    """
    from datetime import datetime

    folder = str(Path(source_path).parent) + os.sep

    # file_size_bytes — DB first, filesystem fallback
    if db_file_size is not None:
        size: int = db_file_size
    else:
        try:
            size = int(os.path.getsize(source_path))
        except OSError:
            size = 0

    # shot_date — DB first, Pillow EXIF fallback (only for REVIEW_DUPLICATE)
    if db_shot_date is not None:
        shot = datetime.fromisoformat(db_shot_date)
    elif read_exif:
        shot = get_exif_datetime_original(source_path)
    else:
        shot = None

    # creation_date — DB first, getctime fallback
    if db_creation_date is not None:
        creation = datetime.fromisoformat(db_creation_date)
    else:
        creation = get_filesystem_creation_datetime(source_path)

    # mtime — DB first, getmtime fallback
    if db_mtime is not None:
        modified = datetime.fromisoformat(db_mtime)
    else:
        try:
            modified = datetime.fromtimestamp(os.path.getmtime(source_path))
        except OSError:
            modified = None

    return PhotoRecord(
        group_number=group_number,
        is_mark=is_mark,
        is_locked=is_locked,
        folder_path=folder,
        file_path=source_path,
        capture_date=None,
        modified_date=modified,
        file_size_bytes=size,
        creation_date=creation,
        shot_date=shot,
        action=action,
        user_decision=user_decision,
        hamming_distance=hamming_distance,
        pixel_width=db_pixel_width,
        pixel_height=db_pixel_height,
        phash=db_phash,
        score=db_score,
    )


class ManifestRepository:
    """Read all manifest rows; write user decisions back."""

    # ------------------------------------------------------------------ schema

    def ensure_schema(self, manifest_path: str) -> None:
        """Run the lazy ALTER TABLE migrations on the manifest at
        ``manifest_path``. Idempotent — every ALTER is wrapped in a
        try/except that silently skips columns that already exist.

        Callers that write to columns added by the migration list
        (e.g. ``is_locked``, ``score``, ``user_decision``) MUST call
        this first when the manifest may have been produced by a
        scanner that doesn't know about those columns. ``load()`` calls
        it automatically; post-scan writers (#393's
        ``apply_auto_select_decisions``) call it explicitly because
        ``scanner.manifest.write_manifest`` writes only the original
        DDL and the migrated columns are added on first read.

        Order matters: the additive ADD-COLUMN migrations run FIRST so
        every modern column exists, THEN the #433 drop-move structural
        migration rebuilds the table without ``dest_path`` and converts
        legacy ``action='MOVE'`` rows to '' (undecided). Running the
        drop AFTER the adds guarantees the copy SELECT finds every
        surviving column on the old table.
        """
        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        conn = _connect(manifest_path)
        try:
            for col, ddl in _MIGRATIONS:
                try:
                    conn.execute(
                        f"ALTER TABLE migration_manifest "
                        f"ADD COLUMN {col} {ddl}"
                    )
                    conn.commit()
                except Exception:
                    pass  # column already exists
            self._drop_move_dest_path(conn)
        finally:
            conn.close()

    @staticmethod
    def _drop_move_dest_path(conn: sqlite3.Connection) -> None:
        """#433 — drop the legacy ``dest_path`` column and migrate
        ``action='MOVE'`` rows to '' (undecided).

        Idempotent: the ``PRAGMA table_info`` guard makes this a no-op
        on manifests that never had ``dest_path`` (written by the
        post-#433 scanner) or that have already been migrated.

        Portable: uses the copy-table dance (new table → copy rows →
        drop old → rename) rather than ``ALTER TABLE DROP COLUMN`` so
        it works on SQLite < 3.35. The whole rebuild runs in one
        transaction; the MOVE→'' normalisation is folded into the copy
        ``SELECT`` via ``CASE``. Row count is preserved exactly — no
        row is dropped, only ``dest_path`` and the MOVE label go away.
        """
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(migration_manifest)"
        )}
        if _DROP_MOVE_COLUMN not in cols:
            return  # already migrated / new-schema manifest — no-op

        select_cols = ", ".join(
            "CASE WHEN action = 'MOVE' THEN '' ELSE action END AS action"
            if c == "action" else c
            for c in _POST_DROP_COLUMNS
        )
        insert_cols = ", ".join(_POST_DROP_COLUMNS)

        # Single transaction: build the new table from the canonical DDL,
        # copy every surviving column (MOVE→'' inline), swap names.
        conn.executescript(
            f"""
            BEGIN;
            CREATE TABLE migration_manifest_new (
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
                is_locked        INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO migration_manifest_new ({insert_cols}, is_locked)
                SELECT {select_cols}, is_locked FROM migration_manifest;
            DROP TABLE migration_manifest;
            ALTER TABLE migration_manifest_new RENAME TO migration_manifest;
            CREATE INDEX IF NOT EXISTS idx_source_hash ON migration_manifest(source_hash);
            CREATE INDEX IF NOT EXISTS idx_phash       ON migration_manifest(phash);
            CREATE INDEX IF NOT EXISTS idx_action      ON migration_manifest(action);
            CREATE INDEX IF NOT EXISTS idx_group_id    ON migration_manifest(group_id);
            COMMIT;
            """
        )

    # ------------------------------------------------------------------ load

    def load(self, manifest_path: str) -> Iterator[PhotoRecord]:
        """Yield PhotoRecords for every row in a similarity group (group_id IS NOT NULL).

        Rows are grouped by group_id; each group is assigned a sequential
        group_number.  Groups that end up with only one surviving member (i.e.,
        the partner was removed) are skipped.  Singleton rows (group_id IS NULL,
        e.g. MOVE / UNDATED with no near-duplicate) are not yielded — the UI
        focuses on files that need review.

        Ordering within a group: any action that renders as "Ref" in the
        tree (KEEP / MOVE / UNDATED / unset) → EXACT → REVIEW_DUPLICATE.
        Reference / primary file sits at the top so users scanning a group
        top-down see the "winner" first, then strongest match (#55, #76).
        """
        from collections import defaultdict

        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        # Auto-migrate: add any missing columns. Idempotent — see
        # ensure_schema() docstring for the contract.
        self.ensure_schema(manifest_path)
        conn = _connect(manifest_path)
        conn.row_factory = sqlite3.Row
        try:
            all_rows = conn.execute(_LOAD_ALL_SQL).fetchall()
        finally:
            conn.close()

        # Group rows by group_id, skipping removed and singletons (no group_id).
        by_group: dict[str, list] = defaultdict(list)
        for row in all_rows:
            if (row["user_decision"] or "") == "removed":
                continue
            gid = row["group_id"]
            if gid:
                by_group[gid].append(row)

        # Assign sequential group_number over sorted group_ids; skip orphaned singles.
        group_number = 0
        for gid in sorted(by_group):
            db_rows = by_group[gid]
            if len(db_rows) < 2:
                continue  # partner was removed; skip orphan
            group_number += 1
            for row in db_rows:
                action: str = row["action"]
                source_path: str = row["source_path"]
                user_decision: str = row["user_decision"] or ""
                read_exif = action == "REVIEW_DUPLICATE"
                try:
                    yield _photo_record(
                        source_path=source_path,
                        group_number=group_number,
                        is_mark=False,
                        is_locked=bool(row["is_locked"]),
                        action=action,
                        read_exif=read_exif,
                        user_decision=user_decision,
                        db_file_size=row["file_size_bytes"],
                        db_shot_date=row["shot_date"],
                        db_creation_date=row["creation_date"],
                        db_mtime=row["mtime"],
                        hamming_distance=row["hamming_distance"],
                        db_pixel_width=row["pixel_width"],
                        db_pixel_height=row["pixel_height"],
                        db_phash=row["phash"],
                        db_score=row["score"],
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Skipping {}: {}", source_path, exc)

    # ------------------------------------------------------------------ save

    def save(self, manifest_path: str, groups: Iterable[PhotoGroup]) -> int:
        """Write user_decision for every record back to the manifest."""
        params = [
            (rec.user_decision, rec.file_path)
            for group in groups
            for rec in group.items
        ]
        if not params:
            return 0
        conn = _connect(manifest_path)
        try:
            conn.executemany(_UPDATE_DECISION_SQL, params)
            conn.commit()
        finally:
            conn.close()
        logger.info("Manifest decisions saved: {} rows updated", len(params))
        return len(params)

    def update_decision(self, manifest_path: str, file_path: str, decision: str) -> None:
        """Update user_decision for a single row (right-click set action)."""
        conn = _connect(manifest_path)
        try:
            conn.execute(_UPDATE_DECISION_SQL, (decision, file_path))
            conn.commit()
        finally:
            conn.close()

    def batch_update_decisions(self, manifest_path: str, decisions: dict[str, str]) -> None:
        """Update user_decision for multiple rows in a single transaction."""
        if not decisions:
            return
        conn = _connect(manifest_path)
        try:
            conn.executemany(_UPDATE_DECISION_SQL, [(v, k) for k, v in decisions.items()])
            conn.commit()
        finally:
            conn.close()

    def batch_update_lock_state(self, manifest_path: str, lock_states: dict[str, bool]) -> None:
        """Update is_locked for multiple rows in a single transaction.

        Lock state lives on its own column so it persists independently of
        ``user_decision`` (locking pins the current decision; the two are
        orthogonal at the schema level — see photo-manager#164).
        """
        if not lock_states:
            return
        conn = _connect(manifest_path)
        try:
            conn.executemany(
                _UPDATE_LOCK_SQL,
                [(1 if v else 0, k) for k, v in lock_states.items()],
            )
            conn.commit()
        finally:
            conn.close()

    def batch_update_decisions_and_lock(
        self,
        manifest_path: str,
        decisions: dict[str, str],
        lock_states: dict[str, bool],
    ) -> None:
        """Combined batch — both updates under one connection + commit.

        Same semantics as calling :meth:`batch_update_decisions` followed
        by :meth:`batch_update_lock_state`, but issues one ``COMMIT`` (one
        fsync) instead of two. Used by the post-scan auto-select write
        path (:func:`core.services.auto_select.apply_auto_select_decisions`,
        #393): negligible on local SSD, meaningful over SMB/NAS where the
        per-commit round-trip dominates. Empty dicts on either side are
        skipped; both empty short-circuits before opening the connection.
        """
        if not decisions and not lock_states:
            return
        conn = _connect(manifest_path)
        try:
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
        finally:
            conn.close()

    def rescore(
        self,
        manifest_path: str,
        weights: "dict[str, float] | None" = None,
    ) -> int:
        """Recompute composite scores from cached raw signals (#187).

        Reads every grouped row's scoring-relevant columns
        (``pixel_width/height``, ``file_size_bytes``, ``shot_date``,
        ``mtime``, ``exif_tag_count``, ``gps_present``, ``xmp_derived``,
        plus ``source_path`` and ``group_id``), runs the scorer in
        memory, and writes the new ``score`` values back with a single
        batched UPDATE.

        **No file I/O.** No exiftool subprocess, no Pillow open. Use
        when the user changes ``scoring.weights`` in settings.json —
        avoids a full re-scan, which on a NAS library can take 10+ min.

        Returns the count of rows whose score was written. Isolated rows
        (``group_id IS NULL``) are skipped — they have no peers to score
        against. Live Photo MOV passengers receive score=NULL by the
        scorer's own rule and that NULL is written back.
        """
        from collections import defaultdict
        from scanner.dedup import ManifestRow
        from scanner.scoring import DEFAULT_WEIGHTS, score_group, validate_weights

        if weights is None:
            weights = DEFAULT_WEIGHTS
        validate_weights(weights)

        # Load only the columns the scorer reads — keeps the in-memory
        # rebuild cheap. ``ManifestRow`` requires source_label, action,
        # source_hash, phash, hamming_distance, duplicate_of, reason —
        # those don't affect scoring but the dataclass demands them, so
        # we fill placeholders.
        conn = _connect(manifest_path)
        try:
            rows_data = conn.execute(
                """
                SELECT source_path, action, group_id,
                       pixel_width, pixel_height, file_size_bytes,
                       shot_date, mtime,
                       exif_tag_count, gps_present, xmp_derived
                FROM   migration_manifest
                WHERE  group_id IS NOT NULL
                """
            ).fetchall()
        finally:
            conn.close()

        rows = [
            ManifestRow(
                source_path=r[0],
                source_label="",           # placeholder — not used by scorer
                action=r[1],
                source_hash="",            # placeholder
                phash=None,
                hamming_distance=None,
                duplicate_of=None,
                reason="",
                pixel_width=r[3],
                pixel_height=r[4],
                file_size_bytes=r[5],
                shot_date=r[6],
                mtime=r[7],
                group_id=r[2],
                exif_tag_count=r[8],
                gps_present=bool(r[9]),
                xmp_derived=bool(r[10]),
            )
            for r in rows_data
        ]

        # Group by group_id, score each group, collect (score, path) tuples
        # for the batch UPDATE.
        groups: dict[str, list[ManifestRow]] = defaultdict(list)
        for row in rows:
            groups[row.group_id].append(row)

        updates: list[tuple] = []
        for group_rows in groups.values():
            scores = score_group(group_rows, weights)
            for row in group_rows:
                updates.append((scores[row.source_path], row.source_path))

        if not updates:
            return 0

        conn = _connect(manifest_path)
        try:
            conn.executemany(
                "UPDATE migration_manifest SET score = ? WHERE source_path = ?",
                updates,
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("Rescored {} rows in {}", len(updates), manifest_path)
        return len(updates)

    def mark_executed(self, manifest_path: str, file_paths: list[str]) -> None:
        """Mark a list of rows as executed=1."""
        conn = _connect(manifest_path)
        try:
            conn.executemany(_MARK_EXECUTED_SQL, [(p,) for p in file_paths])
            conn.commit()
        finally:
            conn.close()

    def remove_from_review(self, manifest_path: str, file_paths: list[str]) -> None:
        """Mark rows as removed from the review list (user_decision='removed').

        Removed rows are excluded from future load() calls so they do not
        reappear when the manifest is reopened.  VACUUM is intentionally omitted:
        rows are marked (UPDATE), not deleted, so SQLite has no freed pages to
        reclaim and a VACUUM call would be a no-op at the cost of a full DB rewrite.
        """
        conn = _connect(manifest_path)
        try:
            conn.executemany(_REMOVE_FROM_REVIEW_SQL, [(p,) for p in file_paths])
            conn.commit()
        finally:
            conn.close()
