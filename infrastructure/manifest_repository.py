"""ManifestRepository — loads REVIEW_DUPLICATE groups from migration_manifest.sqlite
into PhotoRecord/PhotoGroup objects for the existing Qt review UI.

Load flow:
  Each REVIEW_DUPLICATE row + its duplicate_of reference → one PhotoGroup (2 items).
  Reference is locked (is_locked=True) to prevent accidental marking.
  Candidate is pre-marked (is_mark=True) since the scanner flagged it as likely duplicate.

Save flow:
  is_mark=True  → action=SKIP,  executed=1  (user confirms it's a duplicate)
  is_mark=False → action=MOVE,  executed=1  (user decided to keep it)
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from loguru import logger

from core.models import PhotoGroup, PhotoRecord
from infrastructure.utils import get_exif_datetime_original, get_filesystem_creation_datetime

_LOAD_SQL = """
SELECT id, source_path, source_label, duplicate_of, hamming_distance, reason, action, executed
FROM   migration_manifest
WHERE  action = 'REVIEW_DUPLICATE'
ORDER  BY hamming_distance, id
"""

_REF_SQL = """
SELECT source_path, source_label, action
FROM   migration_manifest
WHERE  source_path = ?
LIMIT  1
"""

_SAVE_SQL = """
UPDATE migration_manifest
SET    action = ?, executed = 1
WHERE  source_path = ? AND action IN ('REVIEW_DUPLICATE', 'MOVE', 'SKIP')
"""


def _photo_record(
    source_path: str,
    group_number: int,
    is_mark: bool,
    is_locked: bool,
) -> PhotoRecord:
    """Build a PhotoRecord from a source_path, reading metadata from disk.

    Raises FileNotFoundError if the source file does not exist.
    """
    if not Path(source_path).exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    folder = str(Path(source_path).parent) + os.sep
    shot = get_exif_datetime_original(source_path)
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
    )


class ManifestRepository:
    """Read REVIEW_DUPLICATE pairs from manifest; write decisions back."""

    # ------------------------------------------------------------------ load

    def load(self, manifest_path: str) -> Iterator[PhotoRecord]:
        """Yield PhotoRecords for every REVIEW_DUPLICATE pair.

        Each pair uses the manifest row id as group_number so groups remain
        stable if the manifest is re-opened.
        """
        path = Path(manifest_path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        conn = sqlite3.connect(manifest_path)
        conn.row_factory = sqlite3.Row
        try:
            candidates = conn.execute(_LOAD_SQL).fetchall()
            for row in candidates:
                group_number = row["id"]
                already_resolved = row["executed"] == 1

                # Candidate (the near-duplicate flagged by scanner)
                candidate_marked = row["action"] == "SKIP" or (
                    row["action"] == "REVIEW_DUPLICATE" and not already_resolved
                )
                try:
                    yield _photo_record(
                        source_path=row["source_path"],
                        group_number=group_number,
                        is_mark=candidate_marked,
                        is_locked=False,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Skipping candidate {}: {}", row["source_path"], exc)
                    continue

                # Reference (the file the scanner kept as authoritative)
                ref_path = row["duplicate_of"]
                if not ref_path:
                    continue
                try:
                    yield _photo_record(
                        source_path=ref_path,
                        group_number=group_number,
                        is_mark=False,
                        is_locked=True,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Skipping reference {}: {}", ref_path, exc)
        finally:
            conn.close()

    # ------------------------------------------------------------------ save

    def save(self, manifest_path: str, groups: Iterable[PhotoGroup]) -> None:
        """Write user decisions from groups back to the manifest.

        For each non-locked item in each group:
          is_mark=True  → SKIP  (user confirmed duplicate)
          is_mark=False → MOVE  (user wants to keep it)
        """
        conn = sqlite3.connect(manifest_path)
        updated = 0
        try:
            for group in groups:
                for rec in group.items:
                    if rec.is_locked:
                        continue  # reference file — never change its action
                    new_action = "SKIP" if rec.is_mark else "MOVE"
                    cursor = conn.execute(_SAVE_SQL, (new_action, rec.file_path))
                    updated += cursor.rowcount
            conn.commit()
        finally:
            conn.close()
        logger.info("Manifest decisions saved: {} rows updated", updated)
        return updated
