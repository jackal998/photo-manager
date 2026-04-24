"""Tests for scanner/dedup.py — duplicate classification logic."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from scanner.dedup import HashResult, ManifestRow, classify
from scanner.walker import FileRecord


def _dt(year: int = 2024, month: int = 6, day: int = 1) -> datetime:
    return datetime(year, month, day, 12, 0, 0)


def _rows(result: list) -> dict:
    """Index ManifestRows by posix source_path (Windows-safe)."""
    return {Path(r.source_path).as_posix(): r for r in result}


def _record(
    path: str,
    source_label: str = "jdrive",
    file_type: str = "jpeg",
    pair_partner: Path | None = None,
) -> FileRecord:
    return FileRecord(
        path=Path(path),
        source_label=source_label,
        file_type=file_type,
        pair_partner=pair_partner,
    )


def _hr(
    path: str,
    sha256: str = "aaa",
    phash: str | None = "0000000000000000",
    exif_date: datetime | None = None,
    source_label: str = "jdrive",
    file_type: str = "jpeg",
    pair_partner: Path | None = None,
) -> HashResult:
    return HashResult(
        record=_record(path, source_label=source_label, file_type=file_type,
                       pair_partner=pair_partner),
        sha256=sha256,
        phash=phash,
        exif_date=exif_date,
    )


# ---------------------------------------------------------------------------
# EXACT_DUPLICATE
# ---------------------------------------------------------------------------

class TestExactDuplicate:
    def test_lower_priority_source_skipped(self):
        src_a = _hr("/src_a/a.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        src_b = _hr("/src_b/a.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        src_c = _hr("/src_c/a.jpg", sha256="same", source_label="src_c", exif_date=_dt())
        rows = _rows(classify(
            [src_a, src_b, src_c],
            source_priority={"src_a": 0, "src_b": 1, "src_c": 2},
        ))
        assert rows["/src_a/a.jpg"].action == "MOVE"   # survivor — MOVE, not KEEP
        assert rows["/src_b/a.jpg"].action == "EXACT"
        assert rows["/src_c/a.jpg"].action == "EXACT"

    def test_skip_points_to_kept_file(self):
        a = _hr("/jdrive/a.jpg", sha256="x", source_label="jdrive", exif_date=_dt())
        b = _hr("/takeout/b.jpg", sha256="x", source_label="takeout", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"takeout": 0, "jdrive": 1}))
        kept = "/takeout/b.jpg"  # takeout priority 0 > jdrive priority 1
        assert Path(rows["/jdrive/a.jpg"].duplicate_of).as_posix() == kept


# ---------------------------------------------------------------------------
# Dynamic source priority
# ---------------------------------------------------------------------------

class TestDynamicSourcePriority:
    def test_first_source_wins_exact_dup(self):
        """Source with priority 0 wins; lower-priority copy gets EXACT."""
        a = _hr("/src_a/photo.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        b = _hr("/src_b/photo.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"src_a": 0, "src_b": 1}))
        assert rows["/src_a/photo.jpg"].action == "MOVE"
        assert rows["/src_b/photo.jpg"].action == "EXACT"

    def test_second_source_priority_reversed(self):
        """With reversed priority, src_b wins."""
        a = _hr("/src_a/photo.jpg", sha256="same", source_label="src_a", exif_date=_dt())
        b = _hr("/src_b/photo.jpg", sha256="same", source_label="src_b", exif_date=_dt())
        rows = _rows(classify([a, b], source_priority={"src_a": 1, "src_b": 0}))
        assert rows["/src_b/photo.jpg"].action == "MOVE"
        assert rows["/src_a/photo.jpg"].action == "EXACT"

    def test_no_source_priority_auto_infers_from_order(self):
        """Without explicit source_priority, first-seen label gets priority 0."""
        first = _hr("/first/photo.jpg", sha256="dup", source_label="first_src",
                    exif_date=_dt())
        second = _hr("/second/photo.jpg", sha256="dup", source_label="second_src",
                     exif_date=_dt())
        rows = _rows(classify([first, second]))   # no source_priority
        assert rows["/first/photo.jpg"].action == "MOVE"   # first-seen wins
        assert rows["/second/photo.jpg"].action == "EXACT"


# ---------------------------------------------------------------------------
# FORMAT_DUPLICATE
# ---------------------------------------------------------------------------

class TestFormatDuplicate:
    def test_heic_kept_over_jpeg_same_phash(self):
        heic = _hr("/a.heic", sha256="h1", phash="0" * 16, file_type="heic",
                   source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="h2", phash="0" * 16, file_type="jpeg",
                   source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([heic, jpeg]))
        assert rows["/a.heic"].action in ("MOVE", "KEEP")
        assert rows["/a.jpg"].action == "EXACT"

    def test_raw_and_jpeg_both_move(self):
        """RAW + JPEG of same shot must both be kept (complementary rule)."""
        raw = _hr("/a.arw", sha256="r1", phash="0" * 16, file_type="raw",
                  source_label="jdrive", exif_date=_dt())
        jpeg = _hr("/a.jpg", sha256="j1", phash="0" * 16, file_type="jpeg",
                   source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([raw, jpeg]))
        assert rows["/a.arw"].action == "MOVE"
        assert rows["/a.jpg"].action == "MOVE"


# ---------------------------------------------------------------------------
# REVIEW_DUPLICATE (near-duplicate)
# ---------------------------------------------------------------------------

class TestNearDuplicate:
    def test_near_duplicate_flagged(self):
        import imagehash
        base = imagehash.hex_to_hash("0" * 16)
        # Flip 5 bits → hamming distance 5 (within default threshold 10)
        near = imagehash.hex_to_hash("f" + "0" * 15)
        a = _hr("/a.jpg", sha256="s1", phash=str(base), source_label="takeout",
                exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(near), source_label="jdrive",
                exif_date=_dt())
        rows = _rows(classify([a, b], threshold=10))
        assert rows["/b.jpg"].action == "REVIEW_DUPLICATE"

    def test_beyond_threshold_not_flagged(self):
        import imagehash
        # 16-bit difference: hamming distance = 4 (0x000f vs 0x0000)
        h1 = imagehash.hex_to_hash("0" * 16)
        h2 = imagehash.hex_to_hash("000000000000000f")
        a = _hr("/a.jpg", sha256="s1", phash=str(h1), exif_date=_dt())
        b = _hr("/b.jpg", sha256="s2", phash=str(h2), exif_date=_dt())
        # threshold=3 → distance 4 is beyond threshold
        rows = _rows(classify([a, b], threshold=3))
        assert rows["/b.jpg"].action != "REVIEW_DUPLICATE"


# ---------------------------------------------------------------------------
# UNDATED
# ---------------------------------------------------------------------------

class TestUndated:
    def test_no_exif_becomes_undated(self):
        hr = _hr("/jdrive/mystery.jpg", exif_date=None)
        rows = classify([hr])
        assert rows[0].action == "UNDATED"

    def test_undated_file_from_any_source_becomes_undated(self):
        hr = _hr("/any_source/IMG.HEIC", source_label="any_source", exif_date=None)
        rows = classify([hr])
        assert rows[0].action == "UNDATED"


# ---------------------------------------------------------------------------
# Live Photo pair propagation
# ---------------------------------------------------------------------------

class TestLivePhotoPair:
    def test_mov_skipped_when_heic_skipped(self):
        heic_path = Path("/iphone/IMG_1234.HEIC")
        mov_path = Path("/iphone/IMG_1234.MOV")
        orig_heic_path = Path("/jdrive/IMG_1234.HEIC")

        heic = _hr(str(heic_path), sha256="x", source_label="jdrive",
                   file_type="heic", exif_date=_dt(),
                   pair_partner=mov_path)
        mov = _hr(str(mov_path), sha256="y", phash=None,
                  source_label="jdrive", file_type="mov", exif_date=_dt(),
                  pair_partner=heic_path)
        orig = _hr(str(orig_heic_path), sha256="x", source_label="iphone",
                   file_type="heic", exif_date=_dt())

        rows = _rows(classify([heic, mov, orig], source_priority={"iphone": 0, "jdrive": 1}))
        assert rows[heic_path.as_posix()].action == "EXACT"
        assert rows[mov_path.as_posix()].action == "EXACT"


# ---------------------------------------------------------------------------
# dest_path
# ---------------------------------------------------------------------------

class TestDestPath:
    def test_move_has_dest_path(self):
        hr = _hr("/jdrive/IMG.jpg", sha256="u", phash=None,
                 source_label="jdrive", exif_date=_dt(2024, 6, 1))
        rows = classify([hr])
        assert rows[0].action == "MOVE"
        assert rows[0].dest_path == "2024/20240601_jdrive/IMG.jpg"

    def test_skip_has_no_dest_path(self):
        a = _hr("/a.jpg", sha256="dup", source_label="takeout", exif_date=_dt())
        b = _hr("/b.jpg", sha256="dup", source_label="jdrive", exif_date=_dt())
        rows = _rows(classify([a, b]))
        assert rows["/b.jpg"].dest_path is None
