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
    source_hash: str | None = "abc123",
    phash: str | None = None,
    hamming_distance: int | None = None,
    duplicate_of: str | None = None,
    reason: str | None = None,
) -> ManifestRow:
    return ManifestRow(
        source_path=source_path,
        source_label=source_label,
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
        write_manifest([_row("/a/img.jpg", "")], out)
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
            _row("/a/img1.jpg", ""),
            _row("/a/img2.jpg", "REVIEW_DUPLICATE", duplicate_of="/a/img1.jpg", reason="EXACT_DUPLICATE"),
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
        write_manifest([_row("/a.jpg", "")], out)
        gc.collect()  # release Windows file lock from the first connection
        write_manifest([_row("/b.jpg", "REVIEW_DUPLICATE")], out)
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
        write_manifest([_row("/a.jpg", "")], out)
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
                "SELECT phash, hamming_distance FROM migration_manifest"
            ).fetchone()
        assert row == ("aabbccdd", 5)

    def test_group_id_column_exists(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        cols = {r[1] for r in sqlite3.connect(out).execute(
            "PRAGMA table_info(migration_manifest)"
        ).fetchall()}
        assert "group_id" in cols

    def test_duplicate_of_column_absent(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        cols = {r[1] for r in sqlite3.connect(out).execute(
            "PRAGMA table_info(migration_manifest)"
        ).fetchall()}
        assert "duplicate_of" not in cols

    def test_group_id_stored(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="REVIEW_DUPLICATE", source_hash="abc",
            phash="aabbccdd", hamming_distance=5,
            duplicate_of="/b.jpg",  # transient — NOT written to DB
            reason="near-dup",
            group_id="/a.jpg",
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            val = conn.execute("SELECT group_id FROM migration_manifest").fetchone()[0]
        assert val == "/a.jpg"

    def test_orphan_wal_shm_sidecars_removed_on_overwrite(self, tmp_path):
        """#464 — destination's orphan -wal/-shm sidecars from a prior
        writer must not survive a new write_manifest call."""
        import gc
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row("/old.jpg", "")], out)
        gc.collect()
        (tmp_path / "manifest.sqlite-wal").write_bytes(b"orphan-wal-bytes")
        (tmp_path / "manifest.sqlite-shm").write_bytes(b"orphan-shm-bytes")
        write_manifest([_row("/new.jpg", "")], out)
        gc.collect()
        with sqlite3.connect(out) as conn:
            paths = [r[0] for r in conn.execute(
                "SELECT source_path FROM migration_manifest"
            ).fetchall()]
        assert paths == ["/new.jpg"]
        # SQLite may legitimately create fresh sidecars on the next open,
        # but the orphan bytes must be gone (replaced by the temp DB's
        # state, which os.replace transferred to the destination).
        wal = tmp_path / "manifest.sqlite-wal"
        shm = tmp_path / "manifest.sqlite-shm"
        if wal.exists():
            assert wal.read_bytes() != b"orphan-wal-bytes"
        if shm.exists():
            assert shm.read_bytes() != b"orphan-shm-bytes"

    def test_temp_file_not_left_behind_on_success(self, tmp_path):
        """#464 — after a clean write, no <output>.tmp.sqlite (or its own
        sidecars) should remain in output.parent — os.replace moved the
        temp file to the destination."""
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row("/a.jpg", "")], out)
        leftovers = [p.name for p in tmp_path.iterdir() if "tmp.sqlite" in p.name]
        assert leftovers == [], f"temp-write artifacts left behind: {leftovers}"

    def test_destination_unchanged_when_write_fails_midflight(
        self, tmp_path, monkeypatch
    ):
        """#464 — atomic-temp guarantee: if executemany raises mid-write,
        the destination .sqlite is untouched (still has the prior write's
        row), because os.replace only runs after the connection
        successfully commits + closes."""
        import gc
        from scanner import manifest as manifest_mod
        out = tmp_path / "manifest.sqlite"
        write_manifest([_row("/orig.jpg", "")], out)
        gc.collect()

        real_connect = sqlite3.connect

        class _FailingConn:
            """Proxy that forwards everything except executemany, which
            raises. sqlite3.Connection's bound methods are read-only on
            CPython, so a wrapper is the cleanest way to inject failure."""
            def __init__(self, conn):
                self._conn = conn

            def execute(self, *a, **kw):
                return self._conn.execute(*a, **kw)

            def executescript(self, *a, **kw):
                return self._conn.executescript(*a, **kw)

            def executemany(self, *a, **kw):
                raise RuntimeError("simulated mid-write crash")

            def commit(self):
                return self._conn.commit()

            def close(self):
                return self._conn.close()

        def _failing_connect(path, *args, **kwargs):
            return _FailingConn(real_connect(path, *args, **kwargs))

        # Patch sqlite3.connect in manifest module's namespace so only
        # write_manifest sees the failing connect — verification below
        # uses sqlite3.connect from THIS module unchanged.
        monkeypatch.setattr(manifest_mod.sqlite3, "connect", _failing_connect)
        with pytest.raises(RuntimeError, match="simulated mid-write crash"):
            write_manifest(
                [_row("/new.jpg", "")], out
            )
        gc.collect()
        monkeypatch.undo()
        with sqlite3.connect(out) as conn:
            paths = [r[0] for r in conn.execute(
                "SELECT source_path FROM migration_manifest"
            ).fetchall()]
        assert paths == ["/orig.jpg"]

    def test_stale_tmp_sqlite_from_prior_crash_is_replaced(self, tmp_path):
        """#464 — a stale <output>.tmp.sqlite (and its -wal/-shm) from a
        previously-killed run must be removed at the start of the next
        write_manifest call; otherwise sqlite3.connect would lock or
        corrupt the new write on Windows."""
        out = tmp_path / "manifest.sqlite"
        (tmp_path / "manifest.sqlite.tmp.sqlite").write_bytes(b"junk")
        (tmp_path / "manifest.sqlite.tmp.sqlite-wal").write_bytes(b"junk-wal")
        (tmp_path / "manifest.sqlite.tmp.sqlite-shm").write_bytes(b"junk-shm")
        write_manifest([_row("/a.jpg", "")], out)
        with sqlite3.connect(out) as conn:
            paths = [r[0] for r in conn.execute(
                "SELECT source_path FROM migration_manifest"
            ).fetchall()]
        assert paths == ["/a.jpg"]


# ── print_summary ──────────────────────────────────────────────────────────

class TestPrintSummary:
    def test_prints_total(self, capsys):
        rows = [_row(f"/{i}.jpg", "") for i in range(10)]
        print_summary(rows)
        out = capsys.readouterr().out
        assert "10" in out

    def test_counts_each_action(self, capsys):
        # #433 — the MOVE bucket was dropped from the summary. Unique
        # non-duplicate files now carry the empty action ("") and roll up
        # into the "other" line rather than a dedicated bucket.
        rows = (
            [_row(f"/m{i}.jpg", "") for i in range(3)]
            + [_row(f"/s{i}.jpg", "EXACT") for i in range(2)]
            + [_row(f"/r{i}.jpg", "REVIEW_DUPLICATE") for i in range(1)]
        )
        print_summary(rows)
        out = capsys.readouterr().out
        # Friendly labels render instead of raw internal action names
        # (#242 — internal EXACT/REVIEW_DUPLICATE must not leak into the
        # user-visible scan-dialog log).
        assert "exact duplicates" in out
        assert "near-duplicates (review)" in out
        assert "EXACT" not in out
        assert "REVIEW_DUPLICATE" not in out
        # #433 — the MOVE bucket and its label are gone entirely.
        assert "MOVE" not in out
        assert "dated files" not in out
        # #425 negative grep — the old "moved" wording must not reappear.
        assert "to be moved" not in out
        assert "moved" not in out
        # The 3 undecided rows land in the "other" bucket (3 of 6 total).
        assert "other" in out.lower()

    def test_empty_rows_no_crash(self, capsys):
        print_summary([])
        out = capsys.readouterr().out
        assert "0" in out

    # ── #87: headline label + skipped reconciliation ───────────────────────

    def test_headline_label_is_indexed_in_manifest(self, capsys):
        """The headline counts manifest rows, so the label must say so —
        not 'Total files scanned' (which falsely implies files walked +
        hashed). Catches the misleading-label bug from #87."""
        print_summary([_row("/a.jpg", "")])
        out = capsys.readouterr().out
        assert "Indexed in manifest" in out
        assert "Total files scanned" not in out

    def test_skipped_line_omitted_when_zero(self, capsys):
        """No skipped files → no Skipped line (avoids visual noise on the
        happy path)."""
        print_summary([_row("/a.jpg", "")])
        out = capsys.readouterr().out
        assert "Skipped (unreadable)" not in out

    def test_skipped_line_appears_when_nonzero(self, capsys):
        """The whole point of #87: when files were walked + hashed but
        excluded from the manifest, surface the count so the headline
        reconciles with the per-step log lines above."""
        print_summary([], skipped=3)
        out = capsys.readouterr().out
        assert "Skipped (unreadable)" in out
        assert "3" in out

    def test_skipped_zero_value_not_printed(self, capsys):
        """Explicit skipped=0 is the same as the default — no line printed."""
        print_summary([_row("/a.jpg", "")], skipped=0)
        out = capsys.readouterr().out
        assert "Skipped (unreadable)" not in out

    def test_corrupt_only_scenario_reconciles(self, capsys):
        """The exact #87 reproduction: 1 file walked, decode-failed → 0 in
        manifest, 1 skipped. Both numbers must appear so the user can
        reconcile against the 'Hashed 1/1' line earlier in the log."""
        print_summary([], skipped=1)
        out = capsys.readouterr().out
        assert "Indexed in manifest :       0" in out
        assert "Skipped (unreadable):       1" in out


# ── TestManifestSchemaColumns ───────────────────────────────────────────────

class TestManifestSchemaColumns:
    """Scanner stores file metadata in the manifest at write time."""

    def _cols(self, out: Path) -> set[str]:
        with sqlite3.connect(out) as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(migration_manifest)").fetchall()}

    def test_manifest_has_file_size_bytes_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "file_size_bytes" in self._cols(out)

    def test_manifest_has_shot_date_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "shot_date" in self._cols(out)

    def test_manifest_has_creation_date_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "creation_date" in self._cols(out)

    def test_manifest_has_mtime_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "mtime" in self._cols(out)

    def test_write_manifest_stores_file_size_bytes(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="", source_hash="abc",
            phash=None, hamming_distance=None, duplicate_of=None, reason="",
            file_size_bytes=12345,
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            val = conn.execute("SELECT file_size_bytes FROM migration_manifest").fetchone()[0]
        assert val == 12345

    def test_write_manifest_stores_shot_date(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="", source_hash="abc",
            phash=None, hamming_distance=None, duplicate_of=None, reason="",
            shot_date="2023-01-15T10:30:00",
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            val = conn.execute("SELECT shot_date FROM migration_manifest").fetchone()[0]
        assert val == "2023-01-15T10:30:00"

    def test_make_row_populates_file_size(self, tmp_path):
        from unittest.mock import MagicMock
        from scanner.dedup import _make_row, HashResult
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x" * 500)
        hr = HashResult(
            record=MagicMock(path=f, source_label="jdrive", file_type="jpeg", pair_partner=None),
            sha256="abc", phash=None, exif_date=None,
        )
        result = _make_row(hr, "")
        assert result.file_size_bytes == 500

    def test_make_row_populates_shot_date_from_exif_date(self, tmp_path):
        from datetime import datetime
        from unittest.mock import MagicMock
        from scanner.dedup import _make_row, HashResult
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        hr = HashResult(
            record=MagicMock(path=f, source_label="jdrive", file_type="jpeg", pair_partner=None),
            sha256="abc", phash=None, exif_date=datetime(2023, 1, 15, 10, 30, 0),
        )
        result = _make_row(hr, "")
        assert result.shot_date == "2023-01-15T10:30:00"

    def test_make_row_shot_date_none_when_no_exif(self, tmp_path):
        from unittest.mock import MagicMock
        from scanner.dedup import _make_row, HashResult
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        hr = HashResult(
            record=MagicMock(path=f, source_label="jdrive", file_type="jpeg", pair_partner=None),
            sha256="abc", phash=None, exif_date=None,
        )
        result = _make_row(hr, "")
        assert result.shot_date is None


# ── Scoring system schema (#187 — PR 1) ────────────────────────────────────

class TestScoringSchemaColumns:
    """Scoring system adds 4 columns: exif_tag_count, gps_present, xmp_derived, score.

    Raw signals (exif_tag_count, gps_present, xmp_derived) are populated by the
    extended exiftool pass in PR 2. The composite score is written by the scorer
    in PR 3/4. PR 1 establishes the schema and ManifestRow plumbing only — all
    four columns default to NULL or 0 until later PRs populate them.
    """

    def _cols(self, out: Path) -> set[str]:
        with sqlite3.connect(out) as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(migration_manifest)").fetchall()}

    def test_manifest_has_exif_tag_count_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "exif_tag_count" in self._cols(out)

    def test_manifest_has_gps_present_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "gps_present" in self._cols(out)

    def test_manifest_has_xmp_derived_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "xmp_derived" in self._cols(out)

    def test_manifest_has_score_column(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        write_manifest([], out)
        assert "score" in self._cols(out)

    def test_write_manifest_stores_exif_tag_count(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="", source_hash="abc",
            phash=None, hamming_distance=None, duplicate_of=None, reason="",
            exif_tag_count=12,
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            val = conn.execute("SELECT exif_tag_count FROM migration_manifest").fetchone()[0]
        assert val == 12

    def test_write_manifest_stores_gps_present_true(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="", source_hash="abc",
            phash=None, hamming_distance=None, duplicate_of=None, reason="",
            gps_present=True,
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            val = conn.execute("SELECT gps_present FROM migration_manifest").fetchone()[0]
        assert val == 1

    def test_write_manifest_stores_xmp_derived_true(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="", source_hash="abc",
            phash=None, hamming_distance=None, duplicate_of=None, reason="",
            xmp_derived=True,
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            val = conn.execute("SELECT xmp_derived FROM migration_manifest").fetchone()[0]
        assert val == 1

    def test_write_manifest_stores_score(self, tmp_path):
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="REVIEW_DUPLICATE", source_hash="abc",
            phash=None, hamming_distance=None, duplicate_of=None, reason="",
            score=0.875,
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            val = conn.execute("SELECT score FROM migration_manifest").fetchone()[0]
        assert val == pytest.approx(0.875)

    def test_write_manifest_defaults_gps_and_xmp_to_zero(self, tmp_path):
        """Existing callers that don't set the new fields get safe defaults
        (gps_present=0, xmp_derived=0, exif_tag_count=NULL, score=NULL)."""
        out = tmp_path / "manifest.sqlite"
        row = ManifestRow(
            source_path="/a.jpg", source_label="iphone",
            action="", source_hash="abc",
            phash=None, hamming_distance=None, duplicate_of=None, reason="",
        )
        write_manifest([row], out)
        with sqlite3.connect(out) as conn:
            r = conn.execute(
                "SELECT exif_tag_count, gps_present, xmp_derived, score "
                "FROM migration_manifest"
            ).fetchone()
        assert r == (None, 0, 0, None)
