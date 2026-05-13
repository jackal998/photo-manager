"""Core domain models for photo records and groups."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
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
    # Keep-worthiness score in [0.0, 1.0] (#187). None for isolated rows
    # (no peers to score against) and Live Photo MOV passengers (inherit
    # their HEIC's decision). Computed at scan time by scanner.scoring;
    # re-computable without re-scan via ManifestRepository.rescore().
    score: float | None = None


@dataclass
class PhotoGroup:
    """A collection of photo records grouped by `group_number`."""

    group_number: int
    items: list[PhotoRecord] = field(default_factory=list)
    is_expanded: bool = False
