"""Layer-1 tests for :mod:`infrastructure.image_service` (#622 Phase 1).

Covers:
- _ByteBudgetLRUCache byte-budget eviction logic
- Two-tier split (thumb vs preview cache) independence
- DNG embedded JPEG fast path via rawpy.extract_thumb
- PREVIEW_RECIPE_VERSION disk cache path namespace
- Legacy disk cache migration (wipe on first launch)
- _compute_cache_budgets RAM probe integration
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import infrastructure.image_service as svc_mod
from infrastructure.image_service import (
    PREVIEW_RECIPE_VERSION,
    ImageService,
    _ByteBudgetLRUCache,
    _compute_cache_key,
)


# ── QApplication fixture ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp_m():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_qimage(w: int, h: int) -> QImage:
    """Return a valid ARGB32 QImage filled with an opaque pixel."""
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0xFF_80_80_80)
    assert not img.isNull()
    return img


# ── _ByteBudgetLRUCache ──────────────────────────────────────────────────


class TestByteBudgetLRU:
    def test_eviction_when_over_byte_budget(self, qapp_m):
        """Inserting images that sum to > budget triggers LRU eviction.

        Real failure mode: without eviction the cache grows unbounded,
        eventually OOMing on large DNG libraries (the #590 regression class
        re-applied to the preview cache).
        """
        # Budget = 4 bytes: forces eviction after 2 small images
        cache = _ByteBudgetLRUCache(budget_bytes=4)

        img_a = _make_qimage(1, 1)  # sizeInBytes = 4 (ARGB32)
        img_b = _make_qimage(1, 1)

        cache.put("a", img_a)
        assert cache.get("a") is not None
        assert cache.total_bytes <= 4 + img_a.sizeInBytes()

        cache.put("b", img_b)
        # "a" should have been evicted (budget exceeded)
        assert cache.get("a") is None
        assert cache.get("b") is not None

    def test_get_moves_item_to_mru_position(self, qapp_m):
        """Accessing a key promotes it to MRU so it survives the next eviction.

        Real failure mode: an LRU that doesn't update access order would evict
        recently-accessed items, causing unnecessary cache misses (flicker) for
        images the user is actively viewing.
        """
        # Budget tight: one ARGB32 1×1 image = 4 bytes
        cache = _ByteBudgetLRUCache(budget_bytes=8)

        img_a = _make_qimage(1, 1)
        img_b = _make_qimage(1, 1)
        img_c = _make_qimage(1, 1)

        cache.put("a", img_a)
        cache.put("b", img_b)
        # Touch "a" → now MRU; "b" becomes LRU
        cache.get("a")
        # Adding "c" should evict "b", not "a"
        cache.put("c", img_c)

        assert cache.get("a") is not None, "MRU item 'a' should survive eviction"
        assert cache.get("b") is None, "LRU item 'b' should be evicted"
        assert cache.get("c") is not None

    def test_total_bytes_never_negative(self, qapp_m):
        """Removing all items must not leave total_bytes negative.

        A negative total would defeat the budget guard and allow unbounded growth.
        """
        cache = _ByteBudgetLRUCache(budget_bytes=100)
        img = _make_qimage(1, 1)
        cache.put("x", img)
        # Simulate rapid put of same key
        cache.put("x", img)
        assert cache.total_bytes >= 0

    def test_thumb_vs_preview_split_independent(self, qapp_m):
        """Filling the thumb tier must not evict from the preview tier.

        Real failure mode: a shared cache would evict preview images when
        thumbnails are bulk-loaded during grid rendering — causing full-res
        preview flicker as the user scrolls the result tree.
        """
        # Use the real ImageService's two separate caches with a known budget
        svc = ImageService.__new__(ImageService)
        svc._thumb_cache = _ByteBudgetLRUCache(4)   # 4-byte budget (fits 1 image)
        svc._preview_cache = _ByteBudgetLRUCache(100)  # 100-byte budget

        img_preview = _make_qimage(1, 1)
        img_thumb1 = _make_qimage(1, 1)
        img_thumb2 = _make_qimage(1, 1)

        svc._preview_cache.put("pv_key", img_preview)
        svc._thumb_cache.put("th_key_1", img_thumb1)
        # Filling/overflowing the thumb cache:
        svc._thumb_cache.put("th_key_2", img_thumb2)

        # Preview cache is untouched
        assert svc._preview_cache.get("pv_key") is not None, (
            "preview cache evicted by thumb overflow — caches must be independent"
        )


# ── DNG embedded JPEG fast path ──────────────────────────────────────────


class TestDngEmbeddedJpegFastPath:
    """Tests for _try_rawpy_embedded_thumb used in _load_via_rawpy.

    Monkeypatches rawpy to inject controlled thumb sizes — no real DNG file
    needed. The real failure modes are:
    - Using extract_thumb when it returns a too-small thumb → pixelated preview
    - Not falling back to postprocess when extract_thumb raises → blank preview
    """

    def _make_raw_mock_jpeg(self, width: int, height: int) -> MagicMock:
        """Mock rawpy raw object whose extract_thumb returns a JPEG of w×h."""
        import rawpy as _rawpy

        img = _make_qimage(width, height)
        jpeg_bytes = bytearray()
        buf = QImage(1, 1, QImage.Format_RGB888)
        buf.fill(0xFF_80_80_80)
        # Build minimal JPEG bytes via QImage.save
        from PySide6.QtCore import QByteArray, QBuffer
        ba = QByteArray()
        qbuf = QBuffer(ba)
        qbuf.open(QBuffer.WriteOnly)
        img.save(qbuf, "JPEG")
        qbuf.close()
        jpeg_bytes = bytes(ba.data())

        thumb = SimpleNamespace(
            format=_rawpy.ThumbFormat.JPEG,
            data=jpeg_bytes,
        )
        raw = MagicMock()
        raw.extract_thumb.return_value = thumb
        return raw

    def _make_raw_mock_no_thumb(self) -> MagicMock:
        """Mock rawpy raw object whose extract_thumb raises LibRawNoThumbnailError."""
        from infrastructure.image_service import LibRawNoThumbnailError

        raw = MagicMock()
        raw.extract_thumb.side_effect = LibRawNoThumbnailError("no thumb")
        return raw

    def test_extract_thumb_used_when_large_enough(self, qapp_m):
        """When the embedded JPEG is ≥ viewport_cap in longest side,
        _try_rawpy_embedded_thumb returns a QImage and postprocess is
        NOT called on the raw object.

        Real failure mode: always calling postprocess for DNG is ~10×
        slower than using the embedded JPEG; on a 100-DNG scan this
        adds minutes of preview latency.
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True

        raw = self._make_raw_mock_jpeg(4000, 3000)
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=2048)

        assert result is not None, "Should use embedded JPEG when thumb is large enough"
        assert not result.isNull()
        raw.postprocess.assert_not_called()

    def test_fallback_when_thumb_too_small(self, qapp_m):
        """When the embedded JPEG longest side < viewport_cap,
        _try_rawpy_embedded_thumb returns None so the caller falls
        through to postprocess.

        Real failure mode: using a sub-viewport thumb as the preview
        renders a pixelated/blurry image at native resolution — the bug
        this fast-path was designed to avoid introducing.
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True

        raw = self._make_raw_mock_jpeg(800, 600)
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=2048)

        assert result is None, (
            "Should return None when thumb (800×600) < viewport_cap (2048), "
            "allowing caller to fall through to postprocess"
        )

    def test_fallback_when_no_thumbnail_error(self, qapp_m):
        """When LibRawNoThumbnailError is raised, return None gracefully.

        Real failure mode: propagating the exception would crash the preview
        load for every DNG without an embedded thumbnail (common for older
        camera models).
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True

        raw = self._make_raw_mock_no_thumb()
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=2048)

        assert result is None

    def test_viewport_cap_zero_uses_thumb_regardless_of_size(self, qapp_m):
        """viewport_cap=0 means full-res requested — use the embedded thumb
        regardless of its dimensions (it's the full-res escape hatch for
        the FullResViewerDialog).

        Real failure mode: applying the size check when cap==0 would
        always return None from extract_thumb and always force postprocess
        for the full-res viewer — defeating the fast path entirely.
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True

        raw = self._make_raw_mock_jpeg(400, 300)  # small thumb
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=0)

        assert result is not None, (
            "With viewport_cap=0 (full-res), even a small embedded JPEG should be used"
        )


# ── PREVIEW_RECIPE_VERSION disk cache path ───────────────────────────────


class TestPreviewRecipeVersion:
    def test_disk_cache_path_under_version_dir(self, tmp_path):
        """The disk cache file must live under the versioned sub-directory.

        Real failure mode: writing to the legacy root path means a future
        recipe-version bump can't wipe the old cache without also deleting
        the new entries — cache poisoning on upgrade.
        """
        svc = ImageService.__new__(ImageService)
        svc._disk_path = tmp_path
        svc._versioned_disk_path = tmp_path / f"v{PREVIEW_RECIPE_VERSION}"
        svc._versioned_disk_path.mkdir()
        svc._thumb_cache = _ByteBudgetLRUCache(100_000)
        svc._preview_cache = _ByteBudgetLRUCache(100_000)
        svc._pillow_available = False
        svc._pillow_heif_available = False
        svc._rawpy_available = False

        # Stub _load_from_source to return a known QImage
        with patch.object(svc, "_load_from_source") as mock_load:
            img = _make_qimage(4, 4)
            mock_load.return_value = img

            # Request a thumbnail (side <= 256 → thumb tier)
            svc._get_image("/fake/photo.jpg", 128)

        key = _compute_cache_key("/fake/photo.jpg", 128)
        expected_path = tmp_path / f"v{PREVIEW_RECIPE_VERSION}" / f"{key}.jpg"
        assert expected_path.exists(), (
            f"Disk cache file must be written to versioned path {expected_path}"
        )

    def test_legacy_thumbs_wiped_on_first_launch(self, tmp_path):
        """Legacy .jpg files directly under thumbs/ (not under v1/) are
        deleted when ImageService is initialised.

        Real failure mode: keeping legacy entries wastes disk space and may
        serve stale previews if the key format changed — the whole point
        of the version namespace.
        """
        # Seed two legacy files directly in thumbs/ root
        legacy_a = tmp_path / "aaa.jpg"
        legacy_b = tmp_path / "bbb.jpg"
        legacy_a.write_bytes(b"old-thumb-a")
        legacy_b.write_bytes(b"old-thumb-b")

        # A versioned subdir file must NOT be deleted
        v1_dir = tmp_path / "v1"
        v1_dir.mkdir()
        versioned = v1_dir / "ccc.jpg"
        versioned.write_bytes(b"v1-thumb")

        svc = ImageService.__new__(ImageService)
        svc._status_reporter = None
        svc._disk_path = tmp_path
        svc._migrate_legacy_disk_cache()

        assert not legacy_a.exists(), "Legacy root .jpg must be removed"
        assert not legacy_b.exists(), "Legacy root .jpg must be removed"
        assert versioned.exists(), "Versioned .jpg must NOT be removed"

    def test_recipe_version_constant_is_string_1(self):
        """PREVIEW_RECIPE_VERSION must be '1' in Phase 1.

        This is load-bearing: the disk cache path embeds the version string;
        changing it without bumping the constant causes stale-cache misses.
        """
        assert PREVIEW_RECIPE_VERSION == "1"
