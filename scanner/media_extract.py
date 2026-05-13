"""Canonical extraction schema for the scoring system (#187 — PR 2).

Multiple tools cover different file types and fields:

* PIL fills sha256, phash, mean_color, pixel_width/height, exif_date for
  jpeg/png/webp/heic.
* rawpy fills pixel_width/height for RAW (sensor dims, not thumbnail).
* exiftool fills exif_date for all files plus the new scoring signals
  (gps_present, xmp_derived, xmp_rating, exif_tag_count).
* os.stat fills mtime, ctime, file_size_bytes.

Without a canonical contract, every new scoring signal has to be audited
across all four extractor paths to confirm it isn't silently dropped for
some file type. ``MediaExtract`` is that contract.

Sentinel convention (enforced in tests):

  ``None``  — field not attempted by any extractor that owns it.
              After the full pipeline runs, a None on a field that should
              have been populated is a *bug* — detectable, testable.
  ``False`` — field attempted and signal definitively absent.
  ``True``  — signal present.
  value     — signal extracted with this value.

``merge_extracts()`` combines partial extracts (one per tool) into one
canonical ``MediaExtract`` with explicit per-field precedence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class MediaExtract:
    """Canonical output of all extraction tools for one media file.

    Every field is optional so partial extracts (from a single tool) can
    be constructed without setting fields the tool doesn't fill. The
    merge step combines partials with explicit precedence; see
    ``merge_extracts``.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    path: Path
    file_type: str = ""                # 'jpeg' | 'heic' | 'raw' | 'png' | 'mp4' | 'mov' | ...

    # ── Fingerprints (hasher.py — single read_bytes() pass) ──────────────
    sha256: Optional[str] = None
    phash: Optional[str] = None
    mean_color: Optional[str] = None   # "R,G,B"

    # ── Pixel dimensions ──────────────────────────────────────────────────
    # rawpy values override PIL values for RAW (sensor dims, not thumbnail)
    pixel_width: Optional[int] = None
    pixel_height: Optional[int] = None

    # ── Dates ─────────────────────────────────────────────────────────────
    exif_date: Optional[datetime] = None
    exif_date_tag: Optional[str] = None  # which exiftool tag produced exif_date;
                                          # None when PIL was the source (tag not surfaced)
    mtime: Optional[datetime] = None
    ctime: Optional[datetime] = None

    # ── File metadata ─────────────────────────────────────────────────────
    file_size_bytes: Optional[int] = None

    # ── Scoring signals (exiftool extended pass — all file types) ─────────
    exif_tag_count: Optional[int] = None  # None = exiftool not run; 0 = ran, no census tags found
    gps_present: Optional[bool] = None    # None = not checked; False = checked, absent; True = present
    xmp_derived: Optional[bool] = None    # None = not checked; False = checked, absent; True = present
    xmp_rating: Optional[int] = None      # 0–5 if present; None = not present or not checked

    # ── Provenance (for debugging and auditing) ────────────────────────────
    extracted_by: set[str] = field(default_factory=set)
    # Values added by each extractor: "hasher", "pil", "rawpy", "exiftool", "stat"
    extraction_errors: list[str] = field(default_factory=list)
    # Non-fatal issues logged here; fatal failures leave the relevant field None


# Fields handled by the generic first-non-None precedence in merge_extracts.
# Explicitly listed so the precedence contract is greppable and reviewable.
_SIMPLE_FIRST_NON_NONE: tuple[str, ...] = (
    "sha256",
    "phash",
    "mean_color",
    "mtime",
    "ctime",
    "file_size_bytes",
    "exif_tag_count",
    "xmp_rating",
)
_BOOL_FIRST_NON_NONE: tuple[str, ...] = (
    "gps_present",
    "xmp_derived",
)


def merge_extracts(*partials: MediaExtract) -> MediaExtract:
    """Combine partial MediaExtracts into one canonical instance.

    All partials must share the same ``path`` — that is the merge key.
    Provenance metadata (``extracted_by``, ``extraction_errors``) is
    unioned across all partials.

    Precedence rules:

    * ``pixel_width`` / ``pixel_height``: a partial whose ``extracted_by``
      contains ``"rawpy"`` wins outright (sensor dimensions for RAW files
      beat PIL's thumbnail dimensions). Otherwise first non-None wins.
    * ``exif_date`` / ``exif_date_tag``: a partial whose ``extracted_by``
      contains ``"exiftool"`` wins (exiftool's parsing is more reliable
      than PIL's IFD walk and honours XMP/QuickTime tags). Otherwise
      first non-None wins.
    * All other simple fields: first non-None wins.
    * Booleans (``gps_present``, ``xmp_derived``): first non-None wins;
      ``None`` means *not checked* and is skipped, while ``False`` means
      *checked, absent* and is taken.
    * ``file_type``: first non-empty wins.

    Raises ``ValueError`` if no partials are provided or if partials
    reference different paths.
    """
    if not partials:
        raise ValueError("merge_extracts requires at least one partial")
    paths = {p.path for p in partials}
    if len(paths) > 1:
        raise ValueError(
            f"merge_extracts: all partials must share path; got {sorted(paths)!r}"
        )

    out = MediaExtract(path=partials[0].path)

    # Provenance union first so the rawpy/exiftool precedence checks can
    # consult the merged set later if needed.
    for p in partials:
        out.extracted_by.update(p.extracted_by)
        out.extraction_errors.extend(p.extraction_errors)

    # First non-empty wins for file_type.
    for p in partials:
        if not out.file_type and p.file_type:
            out.file_type = p.file_type

    # Simple "first non-None wins" fields.
    for p in partials:
        for attr in _SIMPLE_FIRST_NON_NONE:
            if getattr(out, attr) is None and getattr(p, attr) is not None:
                setattr(out, attr, getattr(p, attr))
        for attr in _BOOL_FIRST_NON_NONE:
            if getattr(out, attr) is None and getattr(p, attr) is not None:
                setattr(out, attr, getattr(p, attr))

    # pixel_width/height — rawpy wins over PIL (sensor dims, not thumbnail).
    rawpy_partial = next(
        (p for p in partials
         if "rawpy" in p.extracted_by and p.pixel_width is not None),
        None,
    )
    if rawpy_partial is not None:
        out.pixel_width = rawpy_partial.pixel_width
        out.pixel_height = rawpy_partial.pixel_height
    else:
        for p in partials:
            if p.pixel_width is not None:
                out.pixel_width = p.pixel_width
                out.pixel_height = p.pixel_height
                break

    # exif_date — exiftool wins over PIL (more reliable parser, XMP-aware).
    exiftool_date_partial = next(
        (p for p in partials
         if "exiftool" in p.extracted_by and p.exif_date is not None),
        None,
    )
    if exiftool_date_partial is not None:
        out.exif_date = exiftool_date_partial.exif_date
        out.exif_date_tag = exiftool_date_partial.exif_date_tag
    else:
        for p in partials:
            if p.exif_date is not None:
                out.exif_date = p.exif_date
                out.exif_date_tag = p.exif_date_tag
                break

    return out
