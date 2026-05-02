"""ManifestRepository — loads all rows from migration_manifest.sqlite
into PhotoRecord/PhotoGroup objects for the Qt review UI.

Load flow:
  Every row is loaded except those with user_decision='removed' (dismissed by
  the user via "Remove from List").  Files with a duplicate_of reference (EXACT /
  REVIEW_DUPLICATE) are grouped with their reference as a pair.  If the reference
  file itself is marked 'removed', the inline yield is also skipped.
  All records load with is_mark=False, is_locked=False.

  EXIF date is only read for REVIEW_DUPLICATE rows (performance).

  If the DB pre-dates the user_decision column, an ALTER TABLE migration runs
  automatically so older manifests open without error.

Save flow:
  Writes rec.user_decision for every record back to the manifest.
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
       action, executed, user_decision,
       file_size_bytes, shot_date, creation_date, mtime,
       pixel_width, pixel_height
FROM   migration_manifest
WHERE  executed = 0
ORDER  BY
    group_id NULLS LAST,
    -- "Ref tier" first (KEEP / MOVE / UNDATED / unset all render as "Ref"
    -- in the tree per app/views/tree_model_builder._file_similarity), then
    -- duplicates by similarity. Putting the reference / primary file at the
    -- top of its group is what users mean when they say "winner first" (#55,
    -- #76). Earlier we moved only KEEP to position 1, but real-world primaries
    -- in dedup groups are almost always MOVE — so KEEP-only didn't move the
    -- displayed Ref to the top.
    CASE action
        WHEN 'KEEP'             THEN 1
        WHEN 'MOVE'             THEN 1
        WHEN 'UNDATED'          THEN 1
        WHEN ''                 THEN 1
        WHEN 'REVIEW_DUPLICATE' THEN 2
        WHEN 'EXACT'            THEN 3
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
]

_UPDATE_DECISION_SQL = """
UPDATE migration_manifest SET user_decision = ? WHERE source_path = ?
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
    )


class ManifestRepository:
    """Read all manifest rows; write user decisions back."""

    # ------------------------------------------------------------------ load

    def load(self, manifest_path: str) -> Iterator[PhotoRecord]:
        """Yield PhotoRecords for every row in a similarity group (group_id IS NOT NULL).

        Rows are grouped by group_id; each group is assigned a sequential
        group_number.  Groups that end up with only one surviving member (i.e.,
        the partner was removed) are skipped.  Singleton rows (group_id IS NULL,
        e.g. MOVE / UNDATED with no near-duplicate) are not yielded — the UI
        focuses on files that need review.

        Ordering within a group: any action that renders as "Ref" in the
        tree (KEEP / MOVE / UNDATED / unset) → REVIEW_DUPLICATE → EXACT.
        Reference / primary file sits at the top so users scanning a group
        top-down see the "winner" first (#55, #76).
        """
        from collections import defaultdict

        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        conn = _connect(manifest_path)
        conn.row_factory = sqlite3.Row
        try:
            # Auto-migrate: add any missing columns (safe to re-run — silently
            # ignored if the column already exists).
            for col, ddl in _MIGRATIONS:
                try:
                    conn.execute(
                        f"ALTER TABLE migration_manifest ADD COLUMN {col} {ddl}"
                    )
                    conn.commit()
                except Exception:
                    pass  # column already exists
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
                        is_locked=False,
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
