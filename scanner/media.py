"""Media type detection, filename parsing, and file-set constants.

Ported from sync_takeout.py with extensions for additional RAW formats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".webp",
    ".tif", ".tiff",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2",
    ".mp4", ".mov", ".m4v", ".avi",
}

PHOTO_EXTENSIONS = MEDIA_EXTENSIONS - {".mp4", ".mov", ".m4v", ".avi"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi"}
RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2"}
LOSSY_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".webp", ".tif", ".tiff"}

SKIP_FILENAMES = {"thumbs.db", "desktop.ini", "failed_inserting_exif.txt", ".ds_store"}

# Google Takeout: "IMG_9556(1).HEIC" → base="IMG_9556", number=1
DUPE_RE = re.compile(r"^(.*)\((\d+)\)$")

# Edited-photo suffixes to strip when matching Live Photo pairs
EDITED_SUFFIXES = ["-已編輯", "(已編輯)", "-edited", "-Edit", "_edited", " edited"]

# Order to try when finding a video's companion image JSON (Live Photo)
COMPANION_PHOTO_EXTS = [".HEIC", ".heic", ".JPG", ".jpg", ".JPEG", ".jpeg", ".PNG", ".png"]


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------

def _magic_type(path: Path) -> Optional[str]:
    """Detect actual file type from magic bytes (first 12 bytes)."""
    try:
        header = path.read_bytes()[:12]
    except OSError:
        return None
    if header[:2] == b"\xff\xd8":
        return "jpeg"
    if header[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12].lower()
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc"):
            return "heic"
        if brand in (b"mp41", b"mp42", b"isom", b"iso2", b"avc1", b"f4v ", b"m4v "):
            return "mp4"
        if brand in (b"qt  ",):
            return "mov"
    return None


def get_file_type(path: Path) -> tuple[str, bool]:
    """Return (file_type, needs_magic_check) for a media file.

    file_type is one of: 'jpeg' | 'heic' | 'raw' | 'png' | 'gif' | 'webp'
                         | 'mp4' | 'mov' | 'skip'
    needs_magic_check is True when the extension was ambiguous and magic bytes
    revealed a different type (caller should be aware the path might be misnamed).
    """
    ext = path.suffix.lower()
    ext_type_map = {
        ".jpg": "jpeg", ".jpeg": "jpeg",
        ".heic": "heic", ".heif": "heic",
        ".dng": "raw", ".cr2": "raw", ".cr3": "raw",
        ".nef": "raw", ".arw": "raw", ".raf": "raw", ".rw2": "raw",
        ".tif": "raw", ".tiff": "raw",
        ".png": "png",
        ".gif": "gif",
        ".webp": "webp",
        ".mp4": "mp4", ".m4v": "mp4",
        ".mov": "mov",
    }
    declared = ext_type_map.get(ext, "skip")

    # Verify magic bytes for formats that can be misnamed
    if declared in ("heic", "raw", "png", "gif", "webp"):
        actual = _magic_type(path)
        if actual and actual != declared:
            return actual, True

    return declared, False


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

@dataclass
class MediaFile:
    """Decomposed media filename for JSON/pair matching."""

    path: Path
    base_stem: str       # stem with (N) stripped
    number: Optional[int]  # N from (N), or None
    suffix: str          # ".HEIC" (preserves original case)
    is_edited: bool
    clean_stem: str      # base_stem with edited suffix stripped


def parse_media_filename(path: Path) -> MediaFile:
    """Decompose a media filename into its parts.

    Handles Google Takeout duplicate numbering (IMG_9556(1).HEIC)
    and edited suffixes (-已編輯, -edited, …).
    """
    stem = path.stem
    suffix = path.suffix

    m = DUPE_RE.match(stem)
    if m:
        base_stem = m.group(1)
        number: Optional[int] = int(m.group(2))
    else:
        base_stem = stem
        number = None

    is_edited = False
    clean_stem = base_stem
    for es in EDITED_SUFFIXES:
        if base_stem.endswith(es):
            clean_stem = base_stem[: -len(es)]
            is_edited = True
            break

    return MediaFile(
        path=path,
        base_stem=base_stem,
        number=number,
        suffix=suffix,
        is_edited=is_edited,
        clean_stem=clean_stem,
    )
