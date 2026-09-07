"""Media type detection and utility functions for video support."""

import os
from pathlib import Path

# Re-export canonical sources from scanner.media so the walked set and
# the display set stay in sync.  The Qt player previously extended this
# with .webm/.mkv; those extensions are not walked so they were dead.
from scanner.media import VIDEO_EXTENSIONS, is_video  # noqa: F401 — re-export


def format_duration(milliseconds: int) -> str:
    """Format duration in milliseconds to MM:SS or HH:MM:SS.

    Args:
        milliseconds: Duration in milliseconds

    Returns:
        str: Formatted duration string
    """
    if milliseconds < 0:
        return "--:--"

    total_seconds = milliseconds // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"


def normalize_windows_path(path: str) -> str:
    """Normalize a file path for Windows and fix drive letter/casing.

    - Converts forward slashes to backslashes
    - Normalizes components (.., .)
    - Upper-cases drive letter if present
    """
    try:
        p = os.path.normpath(path)
        p = p.replace("/", "\\")
        if len(p) >= 2 and p[1] == ":":
            return p[0].upper() + p[1:]
        return p
    except Exception:
        return path
