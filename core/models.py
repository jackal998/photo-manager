from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class PhotoRecord:
    group_number: int
    is_mark: bool
    is_locked: bool
    folder_path: str
    file_path: str
    capture_date: Optional[datetime]
    modified_date: Optional[datetime]
    file_size_bytes: int
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    pixel_height: Optional[int] = None
    pixel_width: Optional[int] = None
    dpi_width: Optional[int] = None
    dpi_height: Optional[int] = None
    orientation: Optional[int] = None


@dataclass
class PhotoGroup:
    group_number: int
    items: List[PhotoRecord] = field(default_factory=list)
    is_expanded: bool = False
