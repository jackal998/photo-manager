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


def compute_phash(path: Path, file_type: str) -> Optional[str]:
    """Compute a 64-bit perceptual hash for image files.

    Returns None for videos and on any loading failure.

    RAW files: try embedded JPEG preview first (fast), fall back to full decode.
    HEIC: requires pillow-heif registered (done at module import).
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
    """Load a PIL Image from a RAW file's embedded JPEG thumbnail.

    Falls back to full rawpy decode if no thumbnail is present.
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
