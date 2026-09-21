"""Canonical extraction schema for the scoring system (#187 — PR 2).

Multiple tools cover different file types and fields:

* PIL fills sha256, phash, mean_color, pixel_width/height, exif_date for
  jpeg/png/webp/heic.
* rawpy fills pixel_width/height for RAW (sensor dims, not thumbnail).
* exiftool fills exif_date for all files plus the scoring signals
  (gps_present, xmp_derived, exif_tag_count).
* os.stat fills mtime, ctime, file_size_bytes.

Without a canonical contract, every new scoring signal has to be audited
across all extractor paths to confirm it isn't silently dropped for some
file type. ``MediaExtract`` is that contract.

Sentinel convention (enforced in tests):

  ``None``  — field not attempted by any extractor that owns it.
              After the full pipeline runs, a None on a field that should
              have been populated is a *bug* — detectable, testable.
  ``False`` — field attempted and signal definitively absent.
  ``True``  — signal present.
  value     — signal extracted with this value.

There is intentionally no generic "merge N partial extracts" combinator
here (a prior ``merge_extracts()`` was removed — #786: it was dead in
production, unit-tested only, and its exif_date precedence
(exiftool-wins) contradicted the pipeline's actual behaviour
(PIL-wins-then-exiftool-backfills-None), see
``core/app_service/scan_runner.py``'s post-hash EXIF backfill and
``scanner/dedup.py::HashResult.to_media_extract``). Each producer of a
``MediaExtract`` for the pipeline's ``extracts`` dict — the exiftool
batch pass (``scanner/exif.py::batch_read_extracts``) or the in-memory
JPEG pass (``HashResult.to_media_extract`` + hasher-derived signals,
#786) — writes ONE complete extract per file; there is nothing left to
merge.
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
    # #820 — the sub-second digits and UTC offset that ``exif_date`` cannot
    # carry: ``parse_exif_date`` truncates to whole seconds on purpose, so a
    # burst at 10 fps ties in ``exif_date`` and is separated only by these.
    # Stored as TEXT verbatim; leading zeros are significant ("05" = 50 ms).
    subsec_time_original: Optional[str] = None   # EXIF:SubSecTimeOriginal / 0x9291
    offset_time_original: Optional[str] = None   # EXIF:OffsetTimeOriginal / 0x9011
    mtime: Optional[datetime] = None
    ctime: Optional[datetime] = None

    # ── File metadata ─────────────────────────────────────────────────────
    file_size_bytes: Optional[int] = None

    # ── Scoring signals (exiftool extended pass — all file types) ─────────
    exif_tag_count: Optional[int] = None  # None = exiftool not run; 0 = ran, no census tags found
    gps_present: Optional[bool] = None    # None = not checked; False = checked, absent; True = present
    xmp_derived: Optional[bool] = None    # None = not checked; False = checked, absent; True = present

    # ── Provenance (for debugging and auditing) ────────────────────────────
    extracted_by: set[str] = field(default_factory=set)
    # Values added by each extractor: "hasher", "pil", "rawpy", "exiftool", "stat"
    extraction_errors: list[str] = field(default_factory=list)
    # Non-fatal issues logged here; fatal failures leave the relevant field None
