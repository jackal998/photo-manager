"""Core domain models for photo records and groups."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PhotoRecord:
    """A single photo row originating from CSV or other sources."""

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


@dataclass
class PhotoGroup:
    """A collection of photo records grouped by `group_number`."""

    group_number: int
    items: list[PhotoRecord] = field(default_factory=list)
    is_expanded: bool = False
