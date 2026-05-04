"""Write and summarise the migration manifest SQLite database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scanner.dedup import ManifestRow

_DDL = """
CREATE TABLE IF NOT EXISTS migration_manifest (
    id               INTEGER PRIMARY KEY,
    source_path      TEXT    NOT NULL,
    source_label     TEXT    NOT NULL,
    dest_path        TEXT,
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
    pixel_height     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_source_hash ON migration_manifest(source_hash);
CREATE INDEX IF NOT EXISTS idx_phash       ON migration_manifest(phash);
CREATE INDEX IF NOT EXISTS idx_action      ON migration_manifest(action);
CREATE INDEX IF NOT EXISTS idx_group_id    ON migration_manifest(group_id);
"""

_INSERT = """
INSERT INTO migration_manifest
    (source_path, source_label, dest_path, action, source_hash,
     phash, hamming_distance, group_id, reason,
     file_size_bytes, shot_date, creation_date, mtime,
     pixel_width, pixel_height)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,  ?, ?, ?, ?, ?, ?)
"""


def write_manifest(rows: list[ManifestRow], output: Path) -> None:
    """Create (or overwrite) the SQLite manifest at output."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with sqlite3.connect(output) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_DDL)
        conn.executemany(
            _INSERT,
            [
                (
                    r.source_path,
                    r.source_label,
                    r.dest_path,
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
                )
                for r in rows
            ],
        )
        conn.commit()


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
    counts: Counter = Counter(r.action for r in rows)
    total = len(rows)

    print("\n── Migration Manifest Summary ──────────────────────")
    print(f"  Indexed in manifest : {total:>7,}")
    if skipped:
        print(f"  Skipped (unreadable): {skipped:>7,}")
    for action in ("KEEP", "MOVE", "EXACT", "REVIEW_DUPLICATE", "UNDATED"):
        n = counts.get(action, 0)
        pct = 100 * n / total if total else 0
        print(f"  {action:<20}: {n:>7,}  ({pct:.1f}%)")
    other = total - sum(counts[a] for a in ("KEEP", "MOVE", "EXACT", "REVIEW_DUPLICATE", "UNDATED"))
    if other:
        print(f"  {'other':<20}: {other:>7,}")
    print("────────────────────────────────────────────────────")

    n_groups = len({r.group_id for r in rows if r.group_id})
    n_grouped = sum(1 for r in rows if r.group_id)
    print(f"\n── Group Summary ───────────────────────────────────")
    print(f"  Groups (≥2 similar files) : {n_groups:>7,}")
    print(f"  Files in groups           : {n_grouped:>7,}")
    print(f"  Isolated (no match)       : {total - n_grouped:>7,}")
    print("────────────────────────────────────────────────────\n")
