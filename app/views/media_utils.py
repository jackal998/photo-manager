"""Media type detection and utility functions for video support."""

import os
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}


def is_video(path: str) -> bool:
    """Check if a file path is a video based on extension.

    Args:
        path: File path to check

    Returns:
        bool: True if the file is a video format
    """
    ext = Path(path).suffix.lower()
    return ext in VIDEO_EXTENSIONS


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
