"""Utilities for date extraction (EXIF and filesystem) and formatting.

This module centralizes date parsing/formatting and metadata extraction so the
rest of the app can depend on a single behavior. It uses best-effort parsing
and will not raise on errors; callers should expect `None` when data is not
available.
"""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any

from loguru import logger

try:
    from PIL import Image  # type: ignore
except ImportError:
    Image = None  # type: ignore

CSV_DT_FMT = "%Y-%m-%d %H:%M:%S"


def parse_csv_datetime(value: str | None) -> datetime | None:
    """Parse timestamp from CSV using CSV_DT_FMT; return None on failure."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), CSV_DT_FMT)
    except (ValueError, TypeError):
        return None


def format_csv_datetime(dt: datetime | None) -> str:
    """Format datetime for CSV; empty string when None."""
    try:
        return dt.strftime(CSV_DT_FMT) if dt else ""
    except (ValueError, TypeError, AttributeError):
        return ""


def get_filesystem_creation_datetime(path: str) -> datetime | None:
    """Best-effort file creation time.

    On Windows, `os.path.getctime` returns creation time. On other systems it may
    return ctime (metadata change). We accept that as a best-effort value.
    """
    try:
        ts = os.path.getctime(path)
        return datetime.fromtimestamp(ts)
    except (OSError, FileNotFoundError, ValueError) as ex:
        logger.debug("getctime failed for {}: {}", path, ex)
        return None


def get_exif_datetime_original(path: str) -> datetime | None:
    """Extract EXIF DateTimeOriginal if available via Pillow.

    Returns None if Pillow is unavailable or EXIF lacks the field.
    """
    if Image is None:
        return None

    try:
        with Image.open(path) as im:
            exif = getattr(im, "getexif", None)
            if not exif:
                return None
            data: Any = exif()
            if not data:
                return None
            # EXIF tag 36867 is DateTimeOriginal, 306 is DateTime
            val = data.get(36867) or data.get(306)
            if not val:
                return None
            # Common EXIF format: "YYYY:MM:DD HH:MM:SS"
            val_str = str(val)
            # Normalize common separators
            if len(val_str) >= 19 and val_str[4] == ":" and val_str[7] == ":":
                dt = datetime.strptime(val_str, "%Y:%m:%d %H:%M:%S")
            else:
                dt = datetime.fromisoformat(val_str.replace("/", "-").replace(".", ":"))
            return dt
    except (OSError, FileNotFoundError, ValueError, TypeError) as ex:
        logger.debug("EXIF read failed for {}: {}", path, ex)
        return None
