"""Tests for scanner/thumbnail.py: the one recipe for the review thumbnail.

Each test names what a user would lose if it failed: the review screen's
picture size, its orientation, or the scan itself dying on a picture it could
otherwise show.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from scanner.thumbnail import (
    DRAFT_UNDERSHOOT,
    THUMBNAIL_SIDE,
    thumbnail_from_image,
    thumbnail_from_jpeg,
)


def _scene(seed: int, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A textured colour picture with hard-edged blocks."""
    rng = np.random.default_rng(seed)
    base = Image.fromarray(rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)).resize(size, Image.BICUBIC)
    texture = Image.fromarray(rng.integers(0, 256, (60, 80), dtype=np.uint8)).resize(size, Image.BILINEAR)
    im = Image.blend(base, Image.merge("RGB", [texture] * 3), 0.35)
    draw = ImageDraw.Draw(im)
    for _ in range(12 if min(size) > 130 else 0):
        x, y = int(rng.integers(0, size[0] - 60)), int(rng.integers(0, size[1] - 60))
        w, h = int(rng.integers(20, 120)), int(rng.integers(20, 120))
        draw.rectangle([x, y, x + w, y + h], fill=tuple(int(v) for v in rng.integers(0, 256, 3)))
    return im


def _jpeg_bytes(picture: Image.Image, **save_kwargs) -> bytes:
    buf = io.BytesIO()
    picture.save(buf, "JPEG", **save_kwargs)
    return buf.getvalue()


def _decoded(data: bytes) -> Image.Image:
    """Decode ``data`` as Pillow reads it back; ``format`` stays set."""
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _write_malformed_exif_jpeg(path: Path) -> None:
    """Write a real, decodable JPEG whose EXIF block cannot be parsed.

    Copied from ``tests/test_scan_runner.py::_write_malformed_exif_jpeg`` (the
    docstring there explains why the TIFF byte-order mark is destroyed and why
    ``dpi=(72, 72)`` is needed to keep Pillow's open-time pre-warm from
    swallowing the parse error).
    """
    img = Image.new("RGB", (32, 32), (200, 30, 30))
    exif = img.getexif()
    exif.get_ifd(0x8769)[36867] = "2024:08:01 12:00:00"
    payload = bytearray(exif.tobytes())
    assert payload[:6] == b"Exif\x00\x00", payload[:6]
    payload[6:8] = b"\x00\x00"
    img.save(str(path), "JPEG", quality=90, exif=bytes(payload), dpi=(72, 72))


class TestThumbnailFromJpeg:
    def test_large_camera_size_jpeg_lands_at_the_review_size(self):
        """A 4032x3024 camera frame must come out at the review screen's 512 px
        long edge, not a full-size or tiny picture: the draft decode must pick
        a reduction that still reaches the cap."""
        big = _scene(1, (4032, 3024))
        data = thumbnail_from_jpeg(_jpeg_bytes(big, quality=90))

        out = _decoded(data)
        assert out.format == "JPEG"
        assert max(out.size) <= THUMBNAIL_SIDE
        assert max(out.size) >= THUMBNAIL_SIDE * (1 - DRAFT_UNDERSHOOT)

    def test_exif_orientation_six_is_applied(self):
        """A landscape 800x600 sensor frame tagged Orientation 6 is a portrait
        picture to the user. The thumbnail must be upright, or every rotated
        phone photo shows sideways in the review grid."""
        exif = Image.Exif()
        exif[0x0112] = 6
        landscape = _scene(2, (800, 600))
        data = thumbnail_from_jpeg(_jpeg_bytes(landscape, quality=90, exif=exif.tobytes()))

        out = _decoded(data)
        assert out.height > out.width

    def test_small_picture_is_not_upscaled(self):
        """A 200x100 picture stays 200x100: a thumbnail that inflates small
        pictures would show them blurred and waste the spill file."""
        small = _scene(3, (200, 100))
        out = _decoded(thumbnail_from_jpeg(_jpeg_bytes(small, quality=90)))
        assert out.size == (200, 100)

    def test_malformed_exif_still_gets_a_thumbnail(self, tmp_path):
        """A decodable JPEG whose EXIF block is damaged must still be shown:
        losing the rotation is acceptable, losing the picture is not."""
        path = tmp_path / "damaged.jpg"
        _write_malformed_exif_jpeg(path)

        out = _decoded(thumbnail_from_jpeg(path.read_bytes()))

        assert out.format == "JPEG"
        assert out.size == (32, 32)  # the 32 px picture, not upscaled and not dropped


class TestThumbnailFromImage:
    def test_shrinks_the_given_picture_in_place(self):
        """The hash pass hands over its working image and relies on it being
        shrunk in place (no full-size copy is kept). The caller reads the new
        size back, so this must hold."""
        picture = _scene(4, (1600, 1200))

        thumbnail_from_image(picture)

        assert max(picture.size) == THUMBNAIL_SIDE

    @pytest.mark.parametrize("mode", ["RGB", "RGBA", "P"])
    def test_any_decoded_mode_gives_an_rgb_jpeg(self, mode):
        """PNG and GIF pictures arrive as RGBA or palette ('P') images. Each
        must still yield a JPEG Pillow reads back as RGB, or those files lose
        their thumbnail and their similarity number."""
        base = _scene(5, (640, 480))
        if mode == "RGBA":
            picture = base.convert("RGBA")
        elif mode == "P":
            picture = base.quantize(colors=64)
        else:
            picture = base

        out = _decoded(thumbnail_from_image(picture))

        assert out.format == "JPEG"
        assert out.mode == "RGB"
        assert max(out.size) == THUMBNAIL_SIDE
