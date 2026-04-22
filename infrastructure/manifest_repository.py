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

_LOAD_ALL_SQL = """
SELECT id, source_path, source_label, duplicate_of, hamming_distance, reason,
       action, executed, user_decision,
       file_size_bytes, shot_date, creation_date, mtime
FROM   migration_manifest
ORDER  BY
    CASE action
        WHEN 'REVIEW_DUPLICATE' THEN 1
        WHEN 'EXACT'            THEN 2
        WHEN 'KEEP'             THEN 3
        WHEN 'UNDATED'          THEN 4
        WHEN 'MOVE'             THEN 5
        ELSE 6
    END,
    hamming_distance,
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
]

_SAVE_SQL = """
UPDATE migration_manifest SET user_decision = ? WHERE source_path = ?
"""

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
    )


class ManifestRepository:
    """Read all manifest rows; write user decisions back."""

    # ------------------------------------------------------------------ load

    def load(self, manifest_path: str) -> Iterator[PhotoRecord]:
        """Yield PhotoRecords for every row in the manifest.

        Ordering: REVIEW_DUPLICATE → EXACT → KEEP → UNDATED → MOVE.
        Paired rows (EXACT / REVIEW_DUPLICATE with duplicate_of) yield
        candidate first, then the reference inline.  Files that already appear
        as references are not also yielded as standalone rows.
        """
        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        conn = sqlite3.connect(manifest_path)
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

        # Paths explicitly removed from the review list — excluded from load.
        # Checked in Python (not SQL) so the inline reference-yield guard can
        # also consult this set.
        removed_paths: set[str] = {
            row["source_path"] for row in all_rows if (row["user_decision"] or "") == "removed"
        }

        # Collect every path that appears as a duplicate_of reference in a pair.
        # Only consider non-removed candidates when building this set, so that
        # a removed candidate's reference can appear as a standalone row.
        ref_paths: set[str] = set()
        for row in all_rows:
            if (row["user_decision"] or "") == "removed":
                continue
            if row["action"] in ("REVIEW_DUPLICATE", "EXACT") and row["duplicate_of"]:
                ref_paths.add(row["duplicate_of"])

        for row in all_rows:
            action: str = row["action"]
            group_number: int = row["id"]
            source_path: str = row["source_path"]
            ref_path: str | None = row["duplicate_of"]
            is_pair = bool(ref_path) and action in ("REVIEW_DUPLICATE", "EXACT")
            user_decision: str = row["user_decision"] or ""

            # Skip rows dismissed via "Remove from List"
            if user_decision == "removed":
                continue

            # Skip standalone emit for files already shown as pair references
            if source_path in ref_paths and not is_pair:
                continue

            # Only read EXIF for REVIEW_DUPLICATE — avoids opening thousands of files
            read_exif = action == "REVIEW_DUPLICATE"

            # DB-cached metadata (None for old manifests → filesystem fallback)
            db_file_size = row["file_size_bytes"]
            db_shot_date = row["shot_date"]
            db_creation_date = row["creation_date"]
            db_mtime = row["mtime"]

            try:
                yield _photo_record(
                    source_path=source_path,
                    group_number=group_number,
                    is_mark=False,
                    is_locked=False,
                    action=action,
                    read_exif=read_exif,
                    user_decision=user_decision,
                    db_file_size=db_file_size,
                    db_shot_date=db_shot_date,
                    db_creation_date=db_creation_date,
                    db_mtime=db_mtime,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Skipping {}: {}", source_path, exc)
                continue

            if is_pair and ref_path and ref_path not in removed_paths:
                # For the reference row, use the same DB metadata if the ref
                # path matches — otherwise fall back (ref may be a different file).
                try:
                    yield _photo_record(
                        source_path=ref_path,
                        group_number=group_number,
                        is_mark=False,
                        is_locked=False,
                        action="",   # reference role — action belongs to the candidate
                        read_exif=read_exif,
                        user_decision="",  # ref has no independent decision
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Skipping reference {}: {}", ref_path, exc)

    # ------------------------------------------------------------------ save

    def save(self, manifest_path: str, groups: Iterable[PhotoGroup]) -> int:
        """Write user_decision for every record back to the manifest."""
        conn = sqlite3.connect(manifest_path)
        updated = 0
        try:
            for group in groups:
                for rec in group.items:
                    cursor = conn.execute(_SAVE_SQL, (rec.user_decision, rec.file_path))
                    updated += cursor.rowcount
            conn.commit()
        finally:
            conn.close()
        logger.info("Manifest decisions saved: {} rows updated", updated)
        return updated

    def update_decision(self, manifest_path: str, file_path: str, decision: str) -> None:
        """Update user_decision for a single row (right-click set action)."""
        conn = sqlite3.connect(manifest_path)
        try:
            conn.execute(_UPDATE_DECISION_SQL, (decision, file_path))
            conn.commit()
        finally:
            conn.close()

    def batch_update_decisions(self, manifest_path: str, decisions: dict[str, str]) -> None:
        """Update user_decision for multiple rows in a single transaction."""
        if not decisions:
            return
        conn = sqlite3.connect(manifest_path)
        try:
            conn.executemany(_UPDATE_DECISION_SQL, [(v, k) for k, v in decisions.items()])
            conn.commit()
        finally:
            conn.close()

    def mark_executed(self, manifest_path: str, file_paths: list[str]) -> None:
        """Mark a list of rows as executed=1."""
        conn = sqlite3.connect(manifest_path)
        try:
            conn.executemany(_MARK_EXECUTED_SQL, [(p,) for p in file_paths])
            conn.commit()
        finally:
            conn.close()

    def remove_from_review(self, manifest_path: str, file_paths: list[str]) -> None:
        """Mark rows as removed from the review list (user_decision='removed').

        Removed rows are excluded from future load() calls so they do not
        reappear when the manifest is reopened.
        """
        conn = sqlite3.connect(manifest_path)
        try:
            conn.executemany(_REMOVE_FROM_REVIEW_SQL, [(p,) for p in file_paths])
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()
