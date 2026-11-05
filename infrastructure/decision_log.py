"""Append-only decision log (WP-D) — one row per manifest row a write changed.

Every guarded write to ``migration_manifest`` (decision, lock, outcome) runs
inside the write's own transaction and is bracketed by this module:

  1. ``snapshot_paths`` / ``snapshot_ids`` copy the old values of the target
     rows into a TEMP table on the same connection;
  2. the existing guarded UPDATE runs, byte-identical to before;
  3. ``append_changes`` inserts one row per target whose user_decision,
     is_locked or outcome actually changed.

Because the diff is keyed on ``migration_manifest.id`` and compares before
against after, a write the guard refused or skipped leaves no row, and
rewriting the same value leaves none either. A failing INSERT raises inside
the same transaction, so the write it describes rolls back with it.

Stdlib-only. The chokepoint that calls this lives in
``infrastructure/manifest_repository.py``; the scan-time writer in
``scanner/manifest.py``.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCES = frozenset({"user", "auto_select", "system"})

VERBS = frozenset({
    "set_decision",
    "lock_toggle",
    "bulk_lock",
    "bulk_decide",
    "apply_best_copy",
    "execute_skip",
    "execute_delete",
    "execute_gate",
    "skip_now",
    "restore",
    "prune",
    "scan_auto_select",
    "reconcile_skip_directory",
})

_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS decision_log (
        id               INTEGER PRIMARY KEY,
        ts               TEXT    NOT NULL,
        batch_id         TEXT    NOT NULL,
        verb             TEXT    NOT NULL,
        source           TEXT    NOT NULL CHECK (source IN ('user', 'auto_select', 'system')),
        row_id           INTEGER NOT NULL,
        source_path      TEXT    NOT NULL,
        source_hash      TEXT,
        group_id         TEXT,
        action           TEXT,
        match_confidence TEXT,
        score            REAL,
        old_decision     TEXT    NOT NULL,
        new_decision     TEXT    NOT NULL,
        old_locked       INTEGER NOT NULL,
        new_locked       INTEGER NOT NULL,
        old_outcome      TEXT    NOT NULL,
        new_outcome      TEXT    NOT NULL,
        confirmed        INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dl_source_hash ON decision_log(source_hash)",
    "CREATE INDEX IF NOT EXISTS idx_dl_source_path ON decision_log(source_path)",
    """
    CREATE TRIGGER IF NOT EXISTS dl_no_update BEFORE UPDATE ON decision_log
    BEGIN SELECT RAISE(ABORT, 'decision_log is append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS dl_no_delete BEFORE DELETE ON decision_log
    BEGIN SELECT RAISE(ABORT, 'decision_log is append-only'); END
    """,
)

# For ``sqlite3.Connection.executescript`` (the scan writer's fresh temp DB).
DDL = ";\n".join(_STATEMENTS) + ";\n"

_TEMP_DDL = (
    "CREATE TEMP TABLE IF NOT EXISTS dl_target "
    "(path TEXT PRIMARY KEY, confirmed INTEGER NOT NULL)",
    "CREATE TEMP TABLE IF NOT EXISTS dl_before "
    "(id INTEGER PRIMARY KEY, old_decision TEXT, old_locked INTEGER, "
    "old_outcome TEXT, confirmed INTEGER)",
)

_SNAPSHOT_TARGET_SQL = "INSERT OR IGNORE INTO dl_target (path, confirmed) VALUES (?, ?)"

# Every manifest row sharing a target path (source_path is not unique).
_SNAPSHOT_PATHS_SQL = """
INSERT INTO dl_before
SELECT m.id, m.user_decision, m.is_locked, m.outcome, t.confirmed
  FROM migration_manifest m JOIN dl_target t ON t.path = m.source_path
"""

_SNAPSHOT_IDS_SQL = """
INSERT OR IGNORE INTO dl_before
SELECT id, user_decision, is_locked, outcome, 0
  FROM migration_manifest WHERE id = ?
"""

_APPEND_SQL = """
INSERT INTO decision_log (ts, batch_id, verb, source, row_id, source_path,
    source_hash, group_id, action, match_confidence, score,
    old_decision, new_decision, old_locked, new_locked,
    old_outcome, new_outcome, confirmed)
SELECT :ts, :batch_id, :verb, :source, m.id, m.source_path,
       m.source_hash, m.group_id, m.action, m.match_confidence, m.score,
       b.old_decision, m.user_decision, b.old_locked, m.is_locked,
       b.old_outcome, m.outcome, b.confirmed
  FROM dl_before b JOIN migration_manifest m ON m.id = b.id
 WHERE b.old_decision IS NOT m.user_decision
    OR b.old_locked IS NOT m.is_locked
    OR b.old_outcome IS NOT m.outcome
 ORDER BY m.id
"""

_COLUMNS = (
    "id, ts, batch_id, verb, source, row_id, source_path, source_hash, "
    "group_id, action, match_confidence, score, old_decision, new_decision, "
    "old_locked, new_locked, old_outcome, new_outcome, confirmed"
)
_READ_ALL_SQL = f"SELECT {_COLUMNS} FROM decision_log ORDER BY id DESC LIMIT ?"
_READ_PATH_SQL = (
    f"SELECT {_COLUMNS} FROM decision_log "
    "WHERE source_path = ? ORDER BY id DESC LIMIT ?"
)
_CARRY_SQL = f"SELECT {_COLUMNS} FROM decision_log ORDER BY id"
_INSERT_CARRIED_SQL = (
    f"INSERT INTO decision_log ({_COLUMNS}) VALUES ({', '.join('?' * 19)})"
)
_TABLE_EXISTS_SQL = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'decision_log'"


@dataclass(frozen=True)
class LogContext:
    """The per-write facts every appended row of one write shares."""

    ts: str
    batch_id: str
    verb: str
    source: str


@dataclass(frozen=True)
class DecisionLogEntry:
    """One appended row, as read back by ``ManifestRepository.decision_log``."""

    id: int
    ts: str
    batch_id: str
    verb: str
    source: str
    row_id: int
    source_path: str
    source_hash: str | None
    group_id: str | None
    action: str | None
    match_confidence: str | None
    score: float | None
    old_decision: str
    new_decision: str
    old_locked: bool
    new_locked: bool
    old_outcome: str
    new_outcome: str
    confirmed: bool


def make_context(*, verb: str, source: str = "user") -> LogContext:
    """Validate ``verb`` / ``source`` and stamp one write's shared facts.

    Raises ValueError before any SQL runs, so a bad verb never opens a txn.
    """
    if verb not in VERBS:
        raise ValueError(f"unknown decision_log verb {verb!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown decision_log source {source!r}")
    return LogContext(
        ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        batch_id=uuid.uuid4().hex,
        verb=verb,
        source=source,
    )


def ensure_decision_log(conn: sqlite3.Connection) -> None:
    """Create the table, indexes and triggers if absent. Idempotent.

    Runs statement by statement, not ``executescript``, because that would
    COMMIT an open transaction; callers may be inside a write_txn.
    """
    for statement in _STATEMENTS:
        conn.execute(statement)


def _prepare_snapshot(conn: sqlite3.Connection) -> None:
    for statement in _TEMP_DDL:
        conn.execute(statement)
    conn.execute("DELETE FROM dl_target")
    conn.execute("DELETE FROM dl_before")


def snapshot_paths(
    conn: sqlite3.Connection, paths: Iterable[str], confirmed: Collection[str]
) -> None:
    """Record the old values of every row whose source_path is in ``paths``.

    ``confirmed`` marks the paths whose locked rows the caller named; it is
    copied into each appended row so a force-unlock is visible as such.
    """
    confirmed_set = frozenset(confirmed)
    _prepare_snapshot(conn)
    conn.executemany(
        _SNAPSHOT_TARGET_SQL,
        [(path, int(path in confirmed_set)) for path in paths],
    )
    conn.execute(_SNAPSHOT_PATHS_SQL)


def snapshot_ids(conn: sqlite3.Connection, ids: Iterable[int]) -> None:
    """Record the old values of the rows with these ``migration_manifest.id``s."""
    _prepare_snapshot(conn)
    conn.executemany(_SNAPSHOT_IDS_SQL, [(row_id,) for row_id in ids])


def append_changes(conn: sqlite3.Connection, ctx: LogContext) -> None:
    """Append one row per snapshotted row whose decision, lock or outcome changed."""
    conn.execute(
        _APPEND_SQL,
        {"ts": ctx.ts, "batch_id": ctx.batch_id, "verb": ctx.verb, "source": ctx.source},
    )


def read(
    conn: sqlite3.Connection, *, source_path: str | None = None, limit: int = 500
) -> list[DecisionLogEntry]:
    """Newest-first entries, optionally for one source_path."""
    if source_path is None:
        rows = conn.execute(_READ_ALL_SQL, (limit,)).fetchall()
    else:
        rows = conn.execute(_READ_PATH_SQL, (source_path, limit)).fetchall()
    return [_entry(row) for row in rows]


def _entry(row: tuple) -> DecisionLogEntry:
    return DecisionLogEntry(
        id=row[0], ts=row[1], batch_id=row[2], verb=row[3], source=row[4],
        row_id=row[5], source_path=row[6], source_hash=row[7], group_id=row[8],
        action=row[9], match_confidence=row[10], score=row[11],
        old_decision=row[12], new_decision=row[13],
        old_locked=bool(row[14]), new_locked=bool(row[15]),
        old_outcome=row[16], new_outcome=row[17], confirmed=bool(row[18]),
    )


def read_carried_rows(manifest: Path) -> list[tuple]:
    """Every decision_log row of an existing manifest, for a scan rewrite.

    A missing file or a manifest with no log table carries nothing. Any other
    failure (an unreadable table, a corrupt file) propagates, so the rewrite
    fails before it can replace the manifest that holds the history.
    """
    if not manifest.exists():
        return []
    # A plain path, never a ``file:`` URI: on Windows ``Path.resolve()`` turns a
    # mapped drive (``J:``) into its UNC form (``\\server\share``) and
    # ``as_uri()`` then yields ``file://server/...``, which SQLite rejects
    # ("invalid uri authority") — every rescan of a NAS manifest would fail
    # after the whole hash pass. ``exists()`` above keeps this from creating a
    # file; ``query_only`` makes the read-only intent explicit.
    conn = sqlite3.connect(str(manifest))
    try:
        conn.execute("PRAGMA query_only = 1")
        if conn.execute(_TABLE_EXISTS_SQL).fetchone() is None:
            return []
        return conn.execute(_CARRY_SQL).fetchall()
    finally:
        conn.close()


def insert_carried(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """Insert carried rows with their original ids, in id order."""
    if rows:
        conn.executemany(_INSERT_CARRIED_SQL, rows)
