"""The one recipe for a review thumbnail.

The review screen shows one picture size: :data:`THUMBNAIL_SIDE` px on the
long side, JPEG quality :data:`THUMBNAIL_QUALITY`. The scan makes that
thumbnail while it holds the file's bytes (``scanner.hasher``) and reads the
similarity signature from it, so no file is decoded a second time for
either. Pillow only, so every layer can import this module.
"""
from __future__ import annotations

import io
import math

from PIL import Image, ImageOps

THUMBNAIL_SIDE = 512
THUMBNAIL_QUALITY = 85

# How far below the cap a draft-mode decode is allowed to land (#865).
# libjpeg's DCT scaler offers only 1/2, 1/4 and 1/8, so a JPEG whose reduced
# size falls just under the cap would decode one whole step larger for want of
# a few pixels: a 4032x3024 frame at 1/8 is 504 px, 1.6 % under a 512 cap.
# Accepting a long edge up to this fraction under the cap takes the smaller
# step. ``thumbnail()`` never upscales, so the output long edge may end up
# this much under the cap.
DRAFT_UNDERSHOOT = 0.02


def draft_target(size: tuple[int, int], cap: int) -> tuple[int, int]:
    """The box to hand ``Image.draft`` for an image of ``size`` at ``cap``.

    Deliberately NOT a square ``(cap, cap)``. ``draft`` picks its reduction from
    ``min(w // req_w, h // req_h)`` — it constrains BOTH axes — while
    ``thumbnail`` fits only the long edge. A square request therefore makes the
    SHORT edge binding and declines reductions that are perfectly fine.
    Measured: a 4032×3024 frame against a square 2008 request stays 4032×3024
    (3024 // 2008 == 1), while the aspect-matched request takes it to 2016×1512.

    So the box is the cap scaled to this image's own aspect ratio, then shrunk
    by :data:`DRAFT_UNDERSHOOT` so a near-miss lands on the next DCT step.
    """
    width, height = size
    longest = max(width, height)
    if longest <= 0:
        return (1, 1)
    factor = cap * (1.0 - DRAFT_UNDERSHOOT) / longest
    return (max(1, math.ceil(width * factor)), max(1, math.ceil(height * factor)))


def thumbnail_from_image(image: Image.Image) -> bytes:
    """JPEG thumbnail of an upright, decoded picture.

    Shrinks ``image`` IN PLACE (no copy of a full-size decode); pass a copy
    if the picture is still needed at its own size. Never upscales.
    """
    image.thumbnail((THUMBNAIL_SIDE, THUMBNAIL_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=THUMBNAIL_QUALITY)
    return out.getvalue()


def thumbnail_from_jpeg(data: bytes) -> bytes:
    """JPEG thumbnail from JPEG bytes, decoded at the smallest libjpeg
    reduction that still meets the cap, with the EXIF Orientation applied."""
    with Image.open(io.BytesIO(data)) as image:
        image.draft("RGB", draft_target(image.size, THUMBNAIL_SIDE))
        try:
            upright = ImageOps.exif_transpose(image)
        except (OSError, ValueError, AttributeError, SyntaxError):
            # Only a malformed EXIF block raises (PIL reports a bad TIFF
            # header as SyntaxError): keep the picture,
            # lose the rotation (the same choice the hash pass makes).
            upright = image
        return thumbnail_from_image(upright)
