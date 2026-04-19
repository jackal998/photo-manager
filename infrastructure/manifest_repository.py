"""ManifestRepository — loads all rows from migration_manifest.sqlite
into PhotoRecord/PhotoGroup objects for the Qt review UI.

Load flow:
  Every row is loaded.  Files with a duplicate_of reference (SKIP /
  REVIEW_DUPLICATE) are grouped with their reference as a pair.
  Files that appear only as references are not duplicated as standalone rows.
  - REVIEW_DUPLICATE candidate  → is_mark=True,  is_locked=False
  - SKIP candidate              → is_mark=True,  is_locked=False
  - KEEP file                   → is_mark=False, is_locked=True
  - Reference in a pair         → is_mark=False, is_locked=True
  - MOVE / UNDATED              → is_mark=False, is_locked=False

  EXIF date is only read for REVIEW_DUPLICATE rows (performance: avoids
  opening every file with Pillow for the thousands of MOVE/SKIP rows).

Save flow:
  Non-locked items only:
    is_mark=True  → action=SKIP,  executed=1
    is_mark=False → action=MOVE,  executed=1
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
SELECT id, source_path, source_label, duplicate_of, hamming_distance, reason, action, executed
FROM   migration_manifest
ORDER  BY
    CASE action
        WHEN 'REVIEW_DUPLICATE' THEN 1
        WHEN 'SKIP'             THEN 2
        WHEN 'KEEP'             THEN 3
        WHEN 'UNDATED'          THEN 4
        WHEN 'MOVE'             THEN 5
        ELSE 6
    END,
    hamming_distance,
    id
"""

_SAVE_SQL = """
UPDATE migration_manifest
SET    action = ?, executed = 1
WHERE  source_path = ? AND action IN ('REVIEW_DUPLICATE', 'MOVE', 'SKIP', 'UNDATED')
"""


def _photo_record(
    source_path: str,
    group_number: int,
    is_mark: bool,
    is_locked: bool,
    action: str = "",
    read_exif: bool = True,
) -> PhotoRecord:
    """Build a PhotoRecord from a source_path, reading metadata from disk.

    Raises FileNotFoundError if the source file does not exist.
    read_exif=False skips the Pillow EXIF call for performance on bulk rows.
    """
    if not Path(source_path).exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    folder = str(Path(source_path).parent) + os.sep
    shot = get_exif_datetime_original(source_path) if read_exif else None
    creation = get_filesystem_creation_datetime(source_path)
    try:
        size = int(os.path.getsize(source_path))
    except OSError:
        size = 0
    try:
        mtime = os.path.getmtime(source_path)
        from datetime import datetime
        modified = datetime.fromtimestamp(mtime)
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
    )


class ManifestRepository:
    """Read all manifest rows; write user decisions back."""

    # ------------------------------------------------------------------ load

    def load(self, manifest_path: str) -> Iterator[PhotoRecord]:
        """Yield PhotoRecords for every row in the manifest.

        Ordering: REVIEW_DUPLICATE → SKIP → KEEP → UNDATED → MOVE.
        Paired rows (SKIP / REVIEW_DUPLICATE with duplicate_of) yield
        candidate first, then locked reference.  Files that already appear
        as references are not also yielded as standalone rows.
        """
        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        conn = sqlite3.connect(manifest_path)
        conn.row_factory = sqlite3.Row
        try:
            all_rows = conn.execute(_LOAD_ALL_SQL).fetchall()
        finally:
            conn.close()

        # Collect every path that appears as a duplicate_of reference in a pair.
        # These will be yielded inline as the reference child of their parent row
        # and must not also appear as standalone single-item rows.
        ref_paths: set[str] = set()
        for row in all_rows:
            if row["action"] in ("REVIEW_DUPLICATE", "SKIP") and row["duplicate_of"]:
                ref_paths.add(row["duplicate_of"])

        for row in all_rows:
            action: str = row["action"]
            group_number: int = row["id"]
            source_path: str = row["source_path"]
            ref_path: str | None = row["duplicate_of"]
            is_pair = bool(ref_path) and action in ("REVIEW_DUPLICATE", "SKIP")

            # Skip standalone emit for files already shown as pair references
            if source_path in ref_paths and not is_pair:
                continue

            is_keep = action == "KEEP"
            # Only read EXIF for REVIEW_DUPLICATE — avoids opening thousands of files
            read_exif = action == "REVIEW_DUPLICATE"
            candidate_marked = action in ("SKIP", "REVIEW_DUPLICATE")

            try:
                yield _photo_record(
                    source_path=source_path,
                    group_number=group_number,
                    is_mark=candidate_marked,
                    is_locked=is_keep,
                    action=action,
                    read_exif=read_exif,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Skipping {}: {}", source_path, exc)
                continue

            if is_pair and ref_path:
                try:
                    yield _photo_record(
                        source_path=ref_path,
                        group_number=group_number,
                        is_mark=False,
                        is_locked=True,
                        action="",        # reference role — action belongs to the candidate
                        read_exif=read_exif,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Skipping reference {}: {}", ref_path, exc)

    # ------------------------------------------------------------------ save

    def save(self, manifest_path: str, groups: Iterable[PhotoGroup]) -> int:
        """Write user decisions from groups back to the manifest.

        For each non-locked item in each group:
          is_mark=True  → SKIP  (user confirmed skip / not worth migrating)
          is_mark=False → MOVE  (user wants this file migrated)
        KEEP rows and reference files (is_locked=True) are never changed.
        """
        conn = sqlite3.connect(manifest_path)
        updated = 0
        try:
            for group in groups:
                for rec in group.items:
                    if rec.is_locked:
                        continue
                    new_action = "SKIP" if rec.is_mark else "MOVE"
                    cursor = conn.execute(_SAVE_SQL, (new_action, rec.file_path))
                    updated += cursor.rowcount
            conn.commit()
        finally:
            conn.close()
        logger.info("Manifest decisions saved: {} rows updated", updated)
        return updated
