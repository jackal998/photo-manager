"""Tests for scanner/hasher.py — SHA-256 and pHash computation."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


def _make_image(color: tuple = (128, 64, 32), size: tuple = (64, 64)) -> Image.Image:
    img = Image.new("RGB", size, color)
    return img


def _write_jpeg(path: Path, color=(128, 64, 32)) -> None:
    _make_image(color).save(path, "JPEG")


def _write_png(path: Path, color=(0, 128, 255)) -> None:
    _make_image(color).save(path, "PNG")


# ---------------------------------------------------------------------------
# compute_sha256
# ---------------------------------------------------------------------------

class TestComputeSha256:
    def test_stable_across_calls(self, tmp_path):
        from scanner.hasher import compute_sha256
        f = tmp_path / "file.bin"
        f.write_bytes(b"hello world")
        assert compute_sha256(f) == compute_sha256(f)

    def test_matches_hashlib(self, tmp_path):
        from scanner.hasher import compute_sha256
        data = b"test content " * 1000
        f = tmp_path / "data.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert compute_sha256(f) == expected

    def test_different_files_differ(self, tmp_path):
        from scanner.hasher import compute_sha256
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert compute_sha256(a) != compute_sha256(b)


# ---------------------------------------------------------------------------
# compute_phash
# ---------------------------------------------------------------------------

class TestComputePhash:
    def test_returns_string_for_jpeg(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "img.jpg"
        _write_jpeg(f)
        result = compute_phash(f, "jpeg")
        assert isinstance(result, str)
        assert len(result) == 16  # 64-bit hash = 16 hex chars

    def test_returns_string_for_png(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "img.png"
        _write_png(f)
        result = compute_phash(f, "png")
        assert isinstance(result, str)

    def test_returns_none_for_video(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "clip.mov"
        f.write_bytes(b"\x00" * 16)
        assert compute_phash(f, "mov") is None
        assert compute_phash(f, "mp4") is None

    def test_returns_none_for_gif(self, tmp_path):
        from scanner.hasher import compute_phash
        f = tmp_path / "anim.gif"
        _make_image().save(f, "GIF")
        assert compute_phash(f, "gif") is None

    def test_same_image_same_hash(self, tmp_path):
        from scanner.hasher import compute_phash
        f1 = tmp_path / "a.jpg"
        f2 = tmp_path / "b.jpg"
        _write_jpeg(f1, color=(200, 100, 50))
        _write_jpeg(f2, color=(200, 100, 50))
        assert compute_phash(f1, "jpeg") == compute_phash(f2, "jpeg")

    def test_different_images_differ(self, tmp_path):
        from scanner.hasher import compute_phash
        f1 = tmp_path / "a.jpg"
        f2 = tmp_path / "b.jpg"
        # Use distinct gradient patterns — solid colors can produce identical pHash
        img1 = Image.new("RGB", (64, 64))
        img2 = Image.new("RGB", (64, 64))
        img1.putdata([(x * 4, 0, 0) for x in range(64 * 64)])          # red gradient
        img2.putdata([(0, 0, x * 4) for x in range(64 * 64)])          # blue gradient
        img1.save(f1, "JPEG", quality=95)
        img2.save(f2, "JPEG", quality=95)
        assert compute_phash(f1, "jpeg") != compute_phash(f2, "jpeg")

    def test_jpeg_png_same_content_same_hash(self, tmp_path):
        """Cross-format: JPEG and PNG of same image should produce same pHash."""
        from scanner.hasher import compute_phash
        img = _make_image(color=(100, 150, 200), size=(128, 128))
        jpg = tmp_path / "img.jpg"
        png = tmp_path / "img.png"
        # Save as JPEG with high quality to minimise compression artefacts
        img.save(jpg, "JPEG", quality=95)
        img.save(png, "PNG")
        h_jpg = compute_phash(jpg, "jpeg")
        h_png = compute_phash(png, "png")
        assert h_jpg is not None and h_png is not None
        # pHash may have small hamming distance due to JPEG artefacts; allow ≤ 4
        import imagehash
        dist = imagehash.hex_to_hash(h_jpg) - imagehash.hex_to_hash(h_png)
        assert dist <= 4, f"Expected cross-format pHash distance ≤ 4, got {dist}"

    def test_raw_embedded_preview(self, tmp_path):
        """RAW with embedded JPEG thumbnail → pHash extracted from thumbnail."""
        from scanner.hasher import compute_phash

        # Mock rawpy so we don't need a real RAW file
        thumb_mock = MagicMock()
        thumb_mock.format = MagicMock()

        buf = io.BytesIO()
        _make_image(color=(80, 160, 40), size=(64, 64)).save(buf, "JPEG")
        thumb_mock.data = buf.getvalue()

        import rawpy as _rawpy  # noqa: F401 (may not be installed)

        with patch("scanner.hasher.rawpy") as mock_rawpy, \
             patch("scanner.hasher._RAWPY_AVAILABLE", True):
            mock_rawpy.ThumbFormat.JPEG = thumb_mock.format
            mock_raw = MagicMock()
            mock_raw.extract_thumb.return_value = thumb_mock
            mock_rawpy.imread.return_value.__enter__ = lambda s: mock_raw
            mock_rawpy.imread.return_value.__exit__ = MagicMock(return_value=False)

            f = tmp_path / "photo.arw"
            f.write_bytes(b"\x00" * 16)
            result = compute_phash(f, "raw")

        assert result is not None
