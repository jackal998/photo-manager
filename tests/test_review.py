"""Tests for review.py — manifest DB helpers for REVIEW_DUPLICATE resolution."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


_DDL = """
CREATE TABLE migration_manifest (
    id               INTEGER PRIMARY KEY,
    source_path      TEXT NOT NULL,
    source_label     TEXT NOT NULL,
    dest_path        TEXT,
    action           TEXT NOT NULL,
    source_hash      TEXT,
    phash            TEXT,
    hamming_distance INTEGER,
    group_id         TEXT,
    reason           TEXT,
    executed         INTEGER NOT NULL DEFAULT 0,
    user_decision    TEXT    NOT NULL DEFAULT ''
);
"""


def _make_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    db_path = tmp_path / "manifest.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_DDL)
        for r in rows:
            cols = list(r.keys())
            col_list = ", ".join(cols)
            placeholders = ", ".join(f":{c}" for c in cols)
            conn.execute(
                f"INSERT INTO migration_manifest ({col_list}) VALUES ({placeholders})",
                r,
            )
        conn.commit()
    return db_path


def _default(overrides: dict) -> dict:
    base = {
        "source_path": "/source/a.jpg",
        "source_label": "jdrive",
        "dest_path": None,
        "action": "REVIEW_DUPLICATE",
        "hamming_distance": 5,
        "group_id": "/group/a",
        "reason": "near-duplicate (hamming=5)",
        "executed": 0,
        "user_decision": "",
    }
    return {**base, **overrides}


class TestPendingReviews:
    def test_returns_only_review_duplicate_rows(self, tmp_path):
        from review import _open, _pending_reviews
        db = _make_manifest(tmp_path, [
            _default({"source_path": "/a.jpg"}),
            _default({"source_path": "/b.jpg", "action": "MOVE"}),
            _default({"source_path": "/c.jpg", "action": "EXACT"}),
        ])
        conn = _open(db)
        rows = _pending_reviews(conn, show_all=False)
        assert len(rows) == 1
        assert rows[0]["source_path"] == "/a.jpg"

    def test_show_all_includes_resolved(self, tmp_path):
        from review import _open, _pending_reviews
        db = _make_manifest(tmp_path, [
            _default({"source_path": "/a.jpg", "executed": 0}),
            _default({"source_path": "/b.jpg", "executed": 1}),
        ])
        conn = _open(db)
        assert len(_pending_reviews(conn, show_all=False)) == 1
        assert len(_pending_reviews(conn, show_all=True)) == 2

    def test_ordered_by_hamming_distance(self, tmp_path):
        from review import _open, _pending_reviews
        db = _make_manifest(tmp_path, [
            _default({"source_path": "/c.jpg", "hamming_distance": 8}),
            _default({"source_path": "/a.jpg", "hamming_distance": 2}),
            _default({"source_path": "/b.jpg", "hamming_distance": 5}),
        ])
        conn = _open(db)
        rows = _pending_reviews(conn, show_all=False)
        distances = [r["hamming_distance"] for r in rows]
        assert distances == sorted(distances)


class TestSetAction:
    def test_skip_resolves_row(self, tmp_path):
        from review import _open, _pending_reviews, _set_action
        db = _make_manifest(tmp_path, [_default({})])
        conn = _open(db)
        row_id = _pending_reviews(conn, show_all=False)[0]["id"]
        _set_action(conn, row_id, "SKIP")
        remaining = _pending_reviews(conn, show_all=False)
        assert remaining == []
        resolved = conn.execute(
            "SELECT action, executed FROM migration_manifest WHERE id = ?", (row_id,)
        ).fetchone()
        assert resolved["action"] == "SKIP"
        assert resolved["executed"] == 1

    def test_move_resolves_row(self, tmp_path):
        from review import _open, _pending_reviews, _set_action
        db = _make_manifest(tmp_path, [_default({})])
        conn = _open(db)
        row_id = _pending_reviews(conn, show_all=False)[0]["id"]
        _set_action(conn, row_id, "MOVE")
        resolved = conn.execute(
            "SELECT action FROM migration_manifest WHERE id = ?", (row_id,)
        ).fetchone()
        assert resolved["action"] == "MOVE"
