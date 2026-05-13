"""Tests for scanner.media_extract — canonical extraction schema (#187 — PR 2).

The MediaExtract dataclass is the single contract every extractor (PIL,
rawpy, exiftool, os.stat) writes against, so no scoring signal is silently
dropped for some file type. ``merge_extracts`` combines partial extracts
with explicit per-field precedence:

  * pixel_width/height — rawpy > PIL (sensor dims, not thumbnail)
  * exif_date — exiftool > PIL (more reliable parser, XMP-aware)
  * everything else — first non-None wins
  * booleans — None = not checked; False = checked absent; True = present

The sentinel convention is the load-bearing part: tests assert that after
the full pipeline runs, fields are not silently None when they should be
False/True. That's the regression we are protecting against.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from scanner.media_extract import MediaExtract, merge_extracts


# ── MediaExtract construction ──────────────────────────────────────────────


class TestMediaExtractConstruction:
    def test_only_path_required(self):
        """Every other field has a safe default — partial extracts construct
        with just the path."""
        ex = MediaExtract(path=Path("/x/a.jpg"))
        assert ex.path == Path("/x/a.jpg")
        assert ex.file_type == ""
        assert ex.sha256 is None
        assert ex.gps_present is None  # sentinel: not checked

    def test_extracted_by_defaults_empty_set(self):
        ex = MediaExtract(path=Path("/x/a.jpg"))
        assert ex.extracted_by == set()
        assert ex.extraction_errors == []

    def test_extracted_by_per_instance(self):
        """Mutable default mistakes (shared set across instances) would
        couple every MediaExtract to every other. Verify isolation."""
        a = MediaExtract(path=Path("/x/a.jpg"))
        b = MediaExtract(path=Path("/x/b.jpg"))
        a.extracted_by.add("pil")
        assert "pil" not in b.extracted_by


# ── merge_extracts: basic mechanics ────────────────────────────────────────


class TestMergeExtractsBasics:
    def test_no_partials_raises(self):
        with pytest.raises(ValueError, match="requires at least one"):
            merge_extracts()

    def test_different_paths_raises(self):
        a = MediaExtract(path=Path("/x/a.jpg"))
        b = MediaExtract(path=Path("/x/b.jpg"))
        with pytest.raises(ValueError, match="must share path"):
            merge_extracts(a, b)

    def test_single_partial_returns_equivalent(self):
        """Merging one partial returns its values (new instance, same data)."""
        a = MediaExtract(
            path=Path("/x/a.jpg"),
            sha256="deadbeef",
            phash="aabbcc",
            extracted_by={"pil", "hasher"},
        )
        merged = merge_extracts(a)
        assert merged.path == a.path
        assert merged.sha256 == "deadbeef"
        assert merged.phash == "aabbcc"
        assert merged.extracted_by == {"pil", "hasher"}

    def test_provenance_union(self):
        """extracted_by and extraction_errors are unioned across partials."""
        a = MediaExtract(
            path=Path("/x/a.jpg"),
            extracted_by={"hasher", "pil"},
            extraction_errors=["pil decode warning"],
        )
        b = MediaExtract(
            path=Path("/x/a.jpg"),
            extracted_by={"exiftool"},
            extraction_errors=["exiftool no GPS"],
        )
        merged = merge_extracts(a, b)
        assert merged.extracted_by == {"hasher", "pil", "exiftool"}
        assert merged.extraction_errors == ["pil decode warning", "exiftool no GPS"]

    def test_file_type_first_non_empty_wins(self):
        a = MediaExtract(path=Path("/x/a.jpg"))  # file_type=""
        b = MediaExtract(path=Path("/x/a.jpg"), file_type="jpeg")
        merged = merge_extracts(a, b)
        assert merged.file_type == "jpeg"


# ── merge_extracts: simple-field precedence ─────────────────────────────────


class TestMergeExtractsSimpleFields:
    def test_first_non_none_wins_for_sha256(self):
        a = MediaExtract(path=Path("/x/a.jpg"), sha256="aaa")
        b = MediaExtract(path=Path("/x/a.jpg"), sha256="bbb")
        merged = merge_extracts(a, b)
        assert merged.sha256 == "aaa"

    def test_none_skipped_for_phash(self):
        """A None partial should not overwrite a later non-None partial."""
        a = MediaExtract(path=Path("/x/a.jpg"))  # phash=None
        b = MediaExtract(path=Path("/x/a.jpg"), phash="abcd")
        merged = merge_extracts(a, b)
        assert merged.phash == "abcd"

    def test_file_size_from_stat_partial(self):
        a = MediaExtract(path=Path("/x/a.jpg"), extracted_by={"hasher"})
        b = MediaExtract(
            path=Path("/x/a.jpg"),
            file_size_bytes=12345,
            extracted_by={"stat"},
        )
        merged = merge_extracts(a, b)
        assert merged.file_size_bytes == 12345


# ── merge_extracts: rawpy beats PIL for pixel dimensions ────────────────────


class TestMergeExtractsRawpyPrecedence:
    def test_rawpy_dims_override_pil_dims(self):
        """For a RAW file, PIL reads the thumbnail (e.g. 1024×768) while
        rawpy reads the sensor (e.g. 6000×4000). rawpy must win."""
        pil_partial = MediaExtract(
            path=Path("/x/photo.nef"),
            pixel_width=1024, pixel_height=768,
            extracted_by={"hasher", "pil"},
        )
        rawpy_partial = MediaExtract(
            path=Path("/x/photo.nef"),
            pixel_width=6000, pixel_height=4000,
            extracted_by={"rawpy"},
        )
        merged = merge_extracts(pil_partial, rawpy_partial)
        assert merged.pixel_width == 6000
        assert merged.pixel_height == 4000

    def test_pil_only_wins_when_no_rawpy(self):
        pil_partial = MediaExtract(
            path=Path("/x/photo.jpg"),
            pixel_width=4032, pixel_height=3024,
            extracted_by={"hasher", "pil"},
        )
        merged = merge_extracts(pil_partial)
        assert merged.pixel_width == 4032
        assert merged.pixel_height == 3024

    def test_rawpy_partial_with_none_dims_skipped(self):
        """If the rawpy partial has None dims (failed extract), PIL's dims
        are used as fallback."""
        pil_partial = MediaExtract(
            path=Path("/x/photo.nef"),
            pixel_width=1024, pixel_height=768,
            extracted_by={"hasher", "pil"},
        )
        rawpy_partial = MediaExtract(
            path=Path("/x/photo.nef"),
            extracted_by={"rawpy"},
            extraction_errors=["rawpy LibRawError"],
        )
        merged = merge_extracts(pil_partial, rawpy_partial)
        assert merged.pixel_width == 1024
        assert merged.pixel_height == 768


# ── merge_extracts: exiftool beats PIL for exif_date ────────────────────────


class TestMergeExtractsExifDatePrecedence:
    def test_exiftool_date_overrides_pil_date(self):
        pil_partial = MediaExtract(
            path=Path("/x/a.jpg"),
            exif_date=datetime(2020, 1, 1, 0, 0, 0),
            extracted_by={"hasher", "pil"},
        )
        exiftool_partial = MediaExtract(
            path=Path("/x/a.jpg"),
            exif_date=datetime(2024, 6, 15, 10, 30, 0),
            exif_date_tag="EXIF:DateTimeOriginal",
            extracted_by={"exiftool"},
        )
        merged = merge_extracts(pil_partial, exiftool_partial)
        assert merged.exif_date == datetime(2024, 6, 15, 10, 30, 0)
        assert merged.exif_date_tag == "EXIF:DateTimeOriginal"

    def test_pil_date_used_when_exiftool_no_date(self):
        """exiftool extraction may produce no date for malformed EXIF — fall
        back to PIL's date if it found one."""
        pil_partial = MediaExtract(
            path=Path("/x/a.jpg"),
            exif_date=datetime(2024, 6, 15, 10, 30, 0),
            extracted_by={"hasher", "pil"},
        )
        exiftool_partial = MediaExtract(
            path=Path("/x/a.jpg"),
            extracted_by={"exiftool"},   # exif_date=None
        )
        merged = merge_extracts(pil_partial, exiftool_partial)
        assert merged.exif_date == datetime(2024, 6, 15, 10, 30, 0)
        # PIL doesn't surface the tag name; exiftool partial had no date.
        assert merged.exif_date_tag is None


# ── merge_extracts: boolean sentinels (None vs False vs True) ───────────────


class TestMergeExtractsBooleanSentinels:
    def test_none_means_not_checked_skipped_in_merge(self):
        """A partial whose gps_present is None must NOT overwrite a partial
        with an explicit False — None means 'not checked', False is data."""
        unchecked = MediaExtract(path=Path("/x/a.jpg"), extracted_by={"hasher"})
        # gps_present defaults to None
        checked_absent = MediaExtract(
            path=Path("/x/a.jpg"),
            gps_present=False,
            extracted_by={"exiftool"},
        )
        merged = merge_extracts(unchecked, checked_absent)
        assert merged.gps_present is False  # NOT None

    def test_false_is_taken_not_skipped(self):
        """The classic mistake: treating False like None and skipping it.
        merge_extracts must take False as a real value."""
        a = MediaExtract(
            path=Path("/x/a.jpg"),
            gps_present=False,
            extracted_by={"exiftool"},
        )
        b = MediaExtract(path=Path("/x/a.jpg"), extracted_by={"hasher"})
        merged = merge_extracts(a, b)
        assert merged.gps_present is False

    def test_first_true_wins(self):
        a = MediaExtract(
            path=Path("/x/a.jpg"),
            gps_present=True,
            extracted_by={"exiftool"},
        )
        b = MediaExtract(
            path=Path("/x/a.jpg"),
            gps_present=False,
            extracted_by={"other"},
        )
        merged = merge_extracts(a, b)
        assert merged.gps_present is True

    def test_xmp_derived_sentinel_preserved(self):
        a = MediaExtract(path=Path("/x/a.jpg"))  # xmp_derived=None
        b = MediaExtract(
            path=Path("/x/a.jpg"),
            xmp_derived=False,
            extracted_by={"exiftool"},
        )
        merged = merge_extracts(a, b)
        assert merged.xmp_derived is False
