"""review.py — Interactive terminal review of REVIEW_DUPLICATE rows.

Shows each near-duplicate pair side-by-side (paths, hamming distance, source
labels) and lets you resolve them: keep the reference (skip the candidate),
keep both, or defer.

Decisions are written back to migration_manifest.sqlite immediately so the
session is resumable.

Usage:
  python review.py --manifest migration_manifest.sqlite
  python review.py --manifest migration_manifest.sqlite --show-all   # re-show resolved rows
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _pending_reviews(conn: sqlite3.Connection, show_all: bool) -> list[sqlite3.Row]:
    where = "" if show_all else "AND executed = 0"
    return conn.execute(
        f"SELECT id, source_path, source_label, duplicate_of, hamming_distance, "
        f"       phash, reason, action, executed "
        f"FROM migration_manifest "
        f"WHERE action = 'REVIEW_DUPLICATE' {where} "
        f"ORDER BY hamming_distance, id"
    ).fetchall()


def _set_action(conn: sqlite3.Connection, row_id: int, action: str) -> None:
    """Update action and mark executed=1 (resolved by human)."""
    conn.execute(
        "UPDATE migration_manifest SET action = ?, executed = 1 WHERE id = ?",
        (action, row_id),
    )
    conn.commit()


def _lookup(conn: sqlite3.Connection, source_path: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT source_path, source_label, action, dest_path "
        "FROM migration_manifest WHERE source_path = ?",
        (source_path,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _fmt_row(row: sqlite3.Row) -> str:
    name = Path(row["source_path"]).name
    label = row["source_label"]
    action = row["action"]
    executed = row["executed"]
    status = "resolved" if executed == 1 else "pending"
    return f"  [{label}] {name}  ({action}, {status})"


def _show_pair(candidate: sqlite3.Row, reference_row: Optional[sqlite3.Row]) -> None:
    dist = candidate["hamming_distance"]
    print(f"\n{'─' * 60}")
    print(f"  hamming distance : {dist}")
    print(f"\n  CANDIDATE (to review):")
    print(f"  [{candidate['source_label']}] {candidate['source_path']}")
    print(f"  reason: {candidate['reason']}")
    print(f"\n  REFERENCE (kept):")
    if reference_row:
        print(f"  [{reference_row['source_label']}] {reference_row['source_path']}")
        print(f"  action: {reference_row['action']}")
    else:
        print(f"  {candidate['duplicate_of']}  (not in manifest)")
    print()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_PROMPT = (
    "  [s] skip candidate (SKIP)  "
    "[k] keep both (MOVE)  "
    "[d] defer  "
    "[q] quit\n  > "
)


def _review_loop(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> None:
    total = len(rows)
    pending = [r for r in rows if r["executed"] == 0]
    print(f"\n{total} REVIEW_DUPLICATE row(s) — {len(pending)} pending resolution.\n")

    for i, candidate in enumerate(rows):
        if candidate["executed"] == 1:
            continue  # already resolved in this session

        reference = _lookup(conn, candidate["duplicate_of"]) if candidate["duplicate_of"] else None
        _show_pair(candidate, reference)

        remaining = sum(1 for r in rows[i:] if r["executed"] == 0)
        print(f"  [{i + 1}/{total}]  {remaining - 1} remaining after this")

        while True:
            try:
                choice = input(_PROMPT).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                return

            if choice == "s":
                _set_action(conn, candidate["id"], "SKIP")
                print("  → SKIP (candidate will not be copied)")
                break
            elif choice == "k":
                _set_action(conn, candidate["id"], "MOVE")
                print("  → MOVE (both files will be copied)")
                break
            elif choice == "d":
                print("  → deferred")
                break
            elif choice == "q":
                print("Quitting.")
                return
            else:
                print("  Invalid choice — use s / k / d / q")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review REVIEW_DUPLICATE rows in migration_manifest.sqlite"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("migration_manifest.sqlite"),
        help="Path to manifest (default: migration_manifest.sqlite)",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show already-resolved rows too",
    )
    args = parser.parse_args()

    conn = _open(args.manifest)
    rows = _pending_reviews(conn, args.show_all)

    if not rows:
        print("No REVIEW_DUPLICATE rows found.")
        return 0

    _review_loop(conn, rows)

    # Final tally
    remaining = conn.execute(
        "SELECT COUNT(*) FROM migration_manifest "
        "WHERE action = 'REVIEW_DUPLICATE' AND executed = 0"
    ).fetchone()[0]
    print(f"\n{remaining} REVIEW_DUPLICATE row(s) still pending.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
