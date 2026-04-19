"""Tests for scanner.manifest — write_manifest and print_summary."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from scanner.dedup import ManifestRow
from scanner.manifest import write_manifest, print_summary


def _row(
    source_path: str,
    action: str,
    source_label: str = "iphone",
    dest_path: str | None = None,
    source_hash: str | None = "abc123",
    phash: str | None = None,
    hamming_distance: int | None = None,
    duplicate_of: str | None = None,
    reason: str | None = None,
) -> ManifestRow:
    return ManifestRow(
        source_path=source_path,
        source_label=source_label,
        dest_path=dest_path,
        action=action,
        source_hash=source_hash,
        phash=phash,
        hamming_distance=hamming_distance,
        duplicate_of=duplicate_of,
        reason=reason,
    )


# ── write_manifest ─────────────────────────────────────────────────────────

class TestWriteManifest:
    def test_creates_sqlite_file(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row("/a/img.jpg", "MOVE", dest_path="/dest/img.jpg")], out)
        assert out.exists()

    def test_table_has_correct_schema(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        with sqlite3.connect(out) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "migration_manifest" in tables

    def test_rows_inserted(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        rows = [
            _row("/a/img1.jpg", "MOVE", dest_path="/dest/img1.jpg"),
            _row("/a/img2.jpg", "SKIP", duplicate_of="/a/img1.jpg", reason="EXACT_DUPLICATE"),
        ]
        write_manifest(rows, out)
        with sqlite3.connect(out) as conn:
            count = conn.execute("SELECT COUNT(*) FROM migration_manifest").fetchone()[0]
        assert count == 2

    def test_action_stored_correctly(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row("/x.jpg", "REVIEW_DUPLICATE")], out)
        with sqlite3.connect(out) as conn:
            action = conn.execute("SELECT action FROM migration_manifest").fetchone()[0]
        assert action == "REVIEW_DUPLICATE"

    def test_overwrites_existing_file(self, tmp_path):
        import gc
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row("/a.jpg", "MOVE", dest_path="/d/a.jpg")], out)
        gc.collect()  # release Windows file lock from the first connection
        write_manifest([_row("/b.jpg", "SKIP")], out)
        gc.collect()
        with sqlite3.connect(out) as conn:
            count = conn.execute("SELECT COUNT(*) FROM migration_manifest").fetchone()[0]
        assert count == 1  # Only the second write's row

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "sub" / "dir" / "manifest.sqlite"
        write_manifest([], out)
        assert out.exists()

    def test_executed_defaults_to_zero(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row("/a.jpg", "MOVE", dest_path="/d/a.jpg")], out)
        with sqlite3.connect(out) as conn:
            executed = conn.execute("SELECT executed FROM migration_manifest").fetchone()[0]
        assert executed == 0

    def test_phash_and_hamming_stored(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row(
            "/a.jpg", "REVIEW_DUPLICATE",
            phash="aabbccdd", hamming_distance=5, duplicate_of="/b.jpg"
        )], out)
        with sqlite3.connect(out) as conn:
            row = conn.execute(
                "SELECT phash, hamming_distance, duplicate_of FROM migration_manifest"
            ).fetchone()
        assert row == ("aabbccdd", 5, "/b.jpg")


# ── print_summary ──────────────────────────────────────────────────────────

class TestPrintSummary:
    def test_prints_total(self, capsys):
        rows = [_row(f"/{i}.jpg", "MOVE", dest_path=f"/d/{i}.jpg") for i in range(10)]
        print_summary(rows)
        out = capsys.readouterr().out
        assert "10" in out

    def test_counts_each_action(self, capsys):
        rows = (
            [_row(f"/m{i}.jpg", "MOVE", dest_path=f"/d/{i}.jpg") for i in range(3)]
            + [_row(f"/s{i}.jpg", "EXACT") for i in range(2)]
            + [_row(f"/r{i}.jpg", "REVIEW_DUPLICATE") for i in range(1)]
        )
        print_summary(rows)
        out = capsys.readouterr().out
        assert "MOVE" in out
        assert "EXACT" in out
        assert "REVIEW_DUPLICATE" in out

    def test_empty_rows_no_crash(self, capsys):
        print_summary([])
        out = capsys.readouterr().out
        assert "0" in out
