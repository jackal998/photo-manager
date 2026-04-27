"""SHA-256 and perceptual hash computation for all media formats."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
    import imagehash
    _HASH_AVAILABLE = True
except ImportError:
    _HASH_AVAILABLE = False

try:
    import rawpy
    _RAWPY_AVAILABLE = True
except ImportError:
    _RAWPY_AVAILABLE = False

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:
    _HEIF_AVAILABLE = False


def compute_sha256(path: Path) -> str:
    """Stream-compute SHA-256 of a file in 64 KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_hashes(
    path: Path, file_type: str
) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[int], Optional[int]]:
    """Single file read: ``(sha256, phash, mean_color, raw_exif_date, width, height)``.

    All six values are derived from one in-memory read — no extra file open.
    ``width``/``height`` are the pixel dimensions of the decoded image (or ``None``
    for video/skip and on decode failure).  For RAW files the embedded JPEG preview
    dimensions are used (accurate for relative comparisons).
    ``mean_color`` is the average RGB via a 1×1 LANCZOS downscale.
    ``raw_date_str`` is ``None`` for RAW/video; callers pass those to exiftool.
    For videos SHA-256 is streamed in 64 KB chunks so large files never load into RAM.
    """
    if file_type in ("mp4", "mov", "gif", "skip"):
        return compute_sha256(path), None, None, None, None, None

    # Single read: derive all six values from memory.
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()

    if not _HASH_AVAILABLE:
        return sha, None, None, None, None, None

    img: Optional[Image.Image] = None
    raw_date: Optional[str] = None
    px_w: Optional[int] = None
    px_h: Optional[int] = None

    if file_type == "raw":
        img = _load_raw_preview_from_bytes(data)
        if img is None:
            # rawpy.open_buffer not available in this version; re-use path-based loader.
            img = _load_raw_preview(path)
        if img is not None:
            px_w, px_h = img.size
        # RAW EXIF dates are not reliably readable via PIL — caller uses exiftool.
    else:
        try:
            with Image.open(io.BytesIO(data)) as pil_img:
                # Extract date BEFORE convert() — that creates a new image without EXIF.
                raw_date = _raw_exif_date(pil_img)
                px_w, px_h = pil_img.size
                img = pil_img.convert("RGB")
                img.load()
        except (OSError, ValueError):
            img = None

    if img is None:
        return sha, None, None, raw_date, None, None
    try:
        tiny = img.resize((1, 1), Image.LANCZOS)
        mc = tiny.getpixel((0, 0))[:3]
        return sha, str(imagehash.phash(img)), f"{mc[0]},{mc[1]},{mc[2]}", raw_date, px_w, px_h
    except (ValueError, TypeError):
        return sha, None, None, raw_date, None, None


def _raw_exif_date(img: "Image.Image") -> Optional[str]:
    """Return the raw ``DateTimeOriginal`` string from a PIL image's EXIF, or None.

    Checks ExifIFD (sub-IFD 0x8769) first, then falls back to main IFD tag 306
    (DateTime).  No parsing — returns the raw "YYYY:MM:DD HH:MM:SS" string so
    that callers can use their own date-parsing logic.
    """
    try:
        exif = img.getexif()
        exif_ifd = exif.get_ifd(0x8769)          # ExifIFD sub-IFD
        raw = exif_ifd.get(36867) or exif.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
        return str(raw) if raw else None
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def compute_phash(path: Path, file_type: str) -> Optional[str]:
    """Compute a 64-bit perceptual hash for image files.

    Returns None for videos and on any loading failure.

    RAW files: try embedded JPEG preview first (fast), fall back to full decode.
    HEIC: requires pillow-heif registered (done at module import).

    Prefer ``compute_hashes()`` when SHA-256 is also needed — it reads the file
    only once instead of twice.
    """
    if not _HASH_AVAILABLE:
        return None
    if file_type in ("mp4", "mov", "gif", "skip"):
        return None

    img: Optional[Image.Image] = None
    if file_type == "raw":
        img = _load_raw_preview(path)
    else:
        try:
            with Image.open(path) as pil_img:
                img = pil_img.convert("RGB")
                img.load()
        except (OSError, ValueError):
            return None

    if img is None:
        return None
    try:
        return str(imagehash.phash(img))
    except (ValueError, TypeError):
        return None


def _load_raw_preview(path: Path) -> Optional[Image.Image]:
    """Load a PIL Image from a RAW file's embedded JPEG thumbnail (path-based).

    Falls back to full rawpy decode if no thumbnail is present.
    Used directly by ``compute_phash``; ``compute_hashes`` prefers
    ``_load_raw_preview_from_bytes`` to avoid a second file read.
    """
    if not _RAWPY_AVAILABLE:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = Image.open(io.BytesIO(thumb.data)).convert("RGB")
                    img.load()
                    return img
            except rawpy.LibRawNoThumbnailError:
                pass
            # Full decode fallback — slower but always works
            rgb = raw.postprocess(use_auto_wb=True, output_bps=8)
            return Image.fromarray(rgb).convert("RGB")
    except (OSError, ValueError):
        return None


def _load_raw_preview_from_bytes(data: bytes) -> Optional[Image.Image]:
    """Load a PIL Image from RAW bytes using rawpy.open_buffer (no second file read).

    Returns None if rawpy.open_buffer is unavailable (older rawpy versions);
    the caller falls back to _load_raw_preview() in that case.
    """
    if not _RAWPY_AVAILABLE:
        return None
    try:
        with rawpy.open_buffer(data) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = Image.open(io.BytesIO(thumb.data)).convert("RGB")
                    img.load()
                    return img
            except rawpy.LibRawNoThumbnailError:
                pass
            rgb = raw.postprocess(use_auto_wb=True, output_bps=8)
            return Image.fromarray(rgb).convert("RGB")
    except (OSError, ValueError, AttributeError):
        # AttributeError → rawpy.open_buffer not available in this rawpy build.
        return None
