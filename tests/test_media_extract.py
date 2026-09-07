"""Tests for scanner.media_extract — canonical extraction schema (#187 — PR 2).

The MediaExtract dataclass is the single contract every extractor (PIL,
rawpy, exiftool, os.stat) writes against, so no scoring signal is silently
dropped for some file type.

The sentinel convention is the load-bearing part: tests assert that after
the full pipeline runs, fields are not silently None when they should be
False/True. That's the regression we are protecting against.

A prior ``merge_extracts()`` combinator (and its tests, formerly here) was
removed in #786 — it was dead in production and its exif_date precedence
contradicted the pipeline's real precedence. See media_extract.py's module
docstring.
"""

from __future__ import annotations

from pathlib import Path

from scanner.media_extract import MediaExtract


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
