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
    duplicate_of     TEXT,
    reason           TEXT,
    executed         INTEGER NOT NULL DEFAULT 0,
    user_decision    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_source_hash ON migration_manifest(source_hash);
CREATE INDEX IF NOT EXISTS idx_phash       ON migration_manifest(phash);
CREATE INDEX IF NOT EXISTS idx_action      ON migration_manifest(action);
"""

_INSERT = """
INSERT INTO migration_manifest
    (source_path, source_label, dest_path, action, source_hash,
     phash, hamming_distance, duplicate_of, reason)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def write_manifest(rows: list[ManifestRow], output: Path) -> None:
    """Create (or overwrite) the SQLite manifest at output."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with sqlite3.connect(output) as conn:
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
                    r.duplicate_of,
                    r.reason,
                )
                for r in rows
            ],
        )
        conn.commit()


def print_summary(rows: list[ManifestRow]) -> None:
    """Print an action-count summary table to stdout."""
    from collections import Counter
    counts: Counter = Counter(r.action for r in rows)
    total = len(rows)

    print("\n── Migration Manifest Summary ──────────────────────")
    print(f"  Total files scanned : {total:>7,}")
    for action in ("KEEP", "MOVE", "EXACT", "REVIEW_DUPLICATE", "UNDATED"):
        n = counts.get(action, 0)
        pct = 100 * n / total if total else 0
        print(f"  {action:<20}: {n:>7,}  ({pct:.1f}%)")
    other = total - sum(counts[a] for a in ("KEEP", "MOVE", "EXACT", "REVIEW_DUPLICATE", "UNDATED"))
    if other:
        print(f"  {'other':<20}: {other:>7,}")
    print("────────────────────────────────────────────────────\n")
