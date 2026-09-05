"""Core domain models for photo records and groups."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class PhotoRecord:
    """A single photo row loaded from a manifest."""

    group_number: int
    is_mark: bool
    is_locked: bool
    folder_path: str
    file_path: str
    capture_date: datetime | None
    modified_date: datetime | None
    file_size_bytes: int
    # New canonical dates
    creation_date: datetime | None = None
    shot_date: datetime | None = None
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    pixel_height: int | None = None
    pixel_width: int | None = None
    dpi_width: int | None = None
    dpi_height: int | None = None
    orientation: int | None = None
    # Scanner classification (populated when loaded from manifest)
    action: str = ""
    # User's planned file operation (delete | keep | "" = undecided)
    user_decision: str = ""
    hamming_distance: int | None = None
    # Perceptual hash hex string (16 chars / 64 bits via imagehash). Used
    # at render time (#253) to recompute the Similarity % against the
    # *displayed* Ref winner, which can diverge from the scanner's anchor
    # after #241's score-aware Ref pick. None for videos, RAW-only rows,
    # and rows from manifests that pre-date the phash column.
    phash: str | None = None
    # Keep-worthiness score in [0.0, 1.0] (#187). None for isolated rows
    # (no peers to score against) and Live Photo MOV passengers (inherit
    # their HEIC's decision). Computed at scan time by scanner.scoring;
    # re-computable without re-scan via ManifestRepository.rescore().
    score: float | None = None
    # Per-dimension scoring signals (#187 raw inputs, surfaced by #680).
    # Types and defaults mirror scanner.dedup.ManifestRow and the manifest
    # columns exactly, so the same value means the same thing at every hop:
    #   exif_tag_count — INTEGER (nullable). None = the extended exiftool
    #     census pass did not run for this file; 0 = it ran and found none.
    #   gps_present / xmp_derived — INTEGER NOT NULL DEFAULT 0, so the DB
    #     cannot express "unknown"; they are plain bools here for the same
    #     reason. False therefore reads as "no GPS / not a derivative" and,
    #     on a manifest written before #187 added the columns, also as "never
    #     measured" — a pre-existing conflation of the storage layer, not one
    #     introduced here.
    # Unlike ``score`` these are independent of grouping: they are populated
    # by extraction, so an unscored row (score=None) still carries them.
    exif_tag_count: int | None = None
    gps_present: bool = False
    xmp_derived: bool = False


@dataclass(slots=True)
class PhotoGroup:
    """A collection of photo records grouped by `group_number`."""

    group_number: int
    items: list[PhotoRecord] = field(default_factory=list)
    is_expanded: bool = False
