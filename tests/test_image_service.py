"""Layer-1 tests for :mod:`infrastructure.image_service` (#622 Phase 1).

Covers:
- _ByteBudgetLRUCache byte-budget eviction logic (bytes interface)
- Two-tier split (thumb vs preview cache) independence
- DNG embedded JPEG fast path via rawpy.extract_thumb
- PREVIEW_RECIPE_VERSION disk cache path namespace
- Legacy disk cache migration (wipe on first launch)
- _compute_cache_budgets RAM probe integration
- bytes return contract: get_thumbnail / get_preview return valid JPEG bytes
- Full-res OOM semaphore: caps concurrent rawpy.postprocess() at 2
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from PIL import Image as PILImage

import infrastructure.image_service as svc_mod
from infrastructure.image_service import (
    PREVIEW_RECIPE_VERSION,
    ImageService,
    _ByteBudgetLRUCache,
    _FULLRES_DECODE_SEM,
    _PLACEHOLDER_JPEG,
    _SHELL_GETIMAGE_FLAG_ATTEMPTS,
    _SIIGBF_BIGGERSIZEOK,
    _SIIGBF_RESIZETOFIT,
    _SIIGBF_SCALEUP,
    _SIIGBF_THUMBNAILONLY,
    _compute_cache_key,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_jpeg(w: int = 4, h: int = 4, color: tuple[int, int, int] = (80, 80, 80)) -> bytes:
    """Return minimal JPEG bytes for a w×h solid-color image."""
    pil = PILImage.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ── _ByteBudgetLRUCache ──────────────────────────────────────────────────


class TestByteBudgetLRU:
    def test_eviction_when_over_byte_budget(self):
        """Inserting items that sum to > budget triggers LRU eviction.

        Real failure mode: without eviction the cache grows unbounded,
        eventually OOMing on large DNG libraries (the #590 regression class
        re-applied to the preview cache).
        """
        jpeg_a = _make_jpeg()
        jpeg_b = _make_jpeg()

        # Budget tight enough that only one item fits
        cache = _ByteBudgetLRUCache(budget_bytes=len(jpeg_a))

        cache.put("a", jpeg_a)
        assert cache.get("a") is not None

        cache.put("b", jpeg_b)
        # "a" should have been evicted (budget exceeded)
        assert cache.get("a") is None
        assert cache.get("b") is not None

    def test_get_moves_item_to_mru_position(self):
        """Accessing a key promotes it to MRU so it survives the next eviction.

        Real failure mode: an LRU that doesn't update access order would evict
        recently-accessed items, causing unnecessary cache misses (flicker) for
        images the user is actively viewing.
        """
        jpeg_a = _make_jpeg()
        jpeg_b = _make_jpeg()
        jpeg_c = _make_jpeg()

        # Budget fits two items
        cache = _ByteBudgetLRUCache(budget_bytes=len(jpeg_a) * 2)

        cache.put("a", jpeg_a)
        cache.put("b", jpeg_b)
        # Touch "a" → now MRU; "b" becomes LRU
        cache.get("a")
        # Adding "c" should evict "b", not "a"
        cache.put("c", jpeg_c)

        assert cache.get("a") is not None, "MRU item 'a' should survive eviction"
        assert cache.get("b") is None, "LRU item 'b' should be evicted"
        assert cache.get("c") is not None

    def test_total_bytes_never_negative(self):
        """Removing all items must not leave total_bytes negative.

        A negative total would defeat the budget guard and allow unbounded growth.
        """
        cache = _ByteBudgetLRUCache(budget_bytes=100_000)
        jpeg = _make_jpeg()
        cache.put("x", jpeg)
        # Simulate rapid put of same key
        cache.put("x", jpeg)
        assert cache.total_bytes >= 0

    def test_clear_evicts_all_entries_and_resets_total_bytes(self):
        """clear() must drop every entry AND reset _total_bytes to 0 so the
        budget accountant doesn't drift. #616 — RAM not released across
        manifest reloads is the user-visible symptom."""
        cache = _ByteBudgetLRUCache(budget_bytes=100_000)
        jpeg = _make_jpeg()
        cache.put("a", jpeg)
        cache.put("b", jpeg)
        cache.put("c", jpeg)
        assert cache.total_bytes > 0
        assert cache.get("a") is not None

        cache.clear()

        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None
        assert cache.total_bytes == 0

    def test_clear_is_safe_on_empty_cache(self):
        """clear() on an empty cache must not raise — it's the default
        state on first construction, and an unconditional clear() on
        manifest unload should be a no-op there."""
        cache = _ByteBudgetLRUCache(budget_bytes=100_000)
        cache.clear()
        assert cache.total_bytes == 0

    def test_thumb_vs_preview_split_independent(self):
        """Filling the thumb tier must not evict from the preview tier.

        Real failure mode: a shared cache would evict preview images when
        thumbnails are bulk-loaded during grid rendering — causing full-res
        preview flicker as the user scrolls the result tree.
        """
        jpeg_preview = _make_jpeg(8, 8)
        jpeg_thumb1 = _make_jpeg()
        jpeg_thumb2 = _make_jpeg()

        # Use the real ImageService's two separate caches with a known budget
        svc = ImageService.__new__(ImageService)
        svc._thumb_cache = _ByteBudgetLRUCache(len(jpeg_thumb1))   # fits 1 item
        svc._preview_cache = _ByteBudgetLRUCache(100_000)

        svc._preview_cache.put("pv_key", jpeg_preview)
        svc._thumb_cache.put("th_key_1", jpeg_thumb1)
        # Overflowing the thumb cache:
        svc._thumb_cache.put("th_key_2", jpeg_thumb2)

        # Preview cache is untouched
        assert svc._preview_cache.get("pv_key") is not None, (
            "preview cache evicted by thumb overflow — caches must be independent"
        )

    def test_image_service_clear_cache_clears_both_tiers(self):
        """ImageService.clear_cache() must clear both _thumb_cache AND
        _preview_cache so RAM from the previous manifest is fully
        released on unload (#616). Clearing only one tier would leak
        whichever side held the larger working set."""
        svc = ImageService.__new__(ImageService)
        svc._thumb_cache = _ByteBudgetLRUCache(100_000)
        svc._preview_cache = _ByteBudgetLRUCache(100_000)
        jpeg = _make_jpeg()
        svc._thumb_cache.put("th", jpeg)
        svc._preview_cache.put("pv", jpeg)
        assert svc._thumb_cache.get("th") is not None
        assert svc._preview_cache.get("pv") is not None

        svc.clear_cache()

        assert svc._thumb_cache.get("th") is None
        assert svc._preview_cache.get("pv") is None
        assert svc._thumb_cache.total_bytes == 0
        assert svc._preview_cache.total_bytes == 0


# ── Bytes return contract ────────────────────────────────────────────────


class TestBytesContract:
    def test_get_image_bytes_returns_jpeg(self, tmp_path):
        """get_thumbnail / get_preview must return bytes that start with the
        JPEG SOI marker (0xFF 0xD8).

        Real failure mode: any code path that returned None or a QImage object
        would cause _ImageTask.run() to pass a non-bytes value to _bytes_to_qimage,
        raising TypeError deep in Qt signal dispatch and silently breaking preview.
        """
        svc = ImageService.__new__(ImageService)
        svc._disk_path = tmp_path
        svc._versioned_disk_path = tmp_path / f"v{PREVIEW_RECIPE_VERSION}"
        svc._versioned_disk_path.mkdir()
        svc._thumb_cache = _ByteBudgetLRUCache(10_000_000)
        svc._preview_cache = _ByteBudgetLRUCache(10_000_000)
        svc._pillow_available = True
        svc._pillow_heif_available = False
        svc._rawpy_available = False

        jpeg = _make_jpeg(64, 64, color=(100, 150, 200))
        jpeg_path = tmp_path / "test_photo.jpg"
        jpeg_path.write_bytes(jpeg)

        result = svc.get_thumbnail(str(jpeg_path), 128)

        assert isinstance(result, bytes), f"get_thumbnail must return bytes, got {type(result)}"
        assert result[:2] == b"\xff\xd8", "Result must start with JPEG SOI marker"

    def test_get_preview_bytes_returns_jpeg(self, tmp_path):
        """get_preview also returns JPEG bytes (same contract as get_thumbnail)."""
        svc = ImageService.__new__(ImageService)
        svc._disk_path = tmp_path
        svc._versioned_disk_path = tmp_path / f"v{PREVIEW_RECIPE_VERSION}"
        svc._versioned_disk_path.mkdir()
        svc._thumb_cache = _ByteBudgetLRUCache(10_000_000)
        svc._preview_cache = _ByteBudgetLRUCache(10_000_000)
        svc._pillow_available = True
        svc._pillow_heif_available = False
        svc._rawpy_available = False

        jpeg = _make_jpeg(128, 128, color=(200, 100, 50))
        jpeg_path = tmp_path / "preview_photo.jpg"
        jpeg_path.write_bytes(jpeg)

        result = svc.get_preview(str(jpeg_path), 512)

        assert isinstance(result, bytes), f"get_preview must return bytes, got {type(result)}"
        assert result[:2] == b"\xff\xd8", "Result must start with JPEG SOI marker"

    def test_placeholder_returned_when_load_fails(self, tmp_path):
        """When source load fails, _PLACEHOLDER_JPEG (bytes) is returned —
        never None, never a QImage.

        Real failure mode: downstream _bytes_to_qimage would crash on None;
        cache.put would store None and report len(None) TypeError.
        """
        svc = ImageService.__new__(ImageService)
        svc._disk_path = tmp_path
        svc._versioned_disk_path = tmp_path / f"v{PREVIEW_RECIPE_VERSION}"
        svc._versioned_disk_path.mkdir()
        svc._thumb_cache = _ByteBudgetLRUCache(10_000_000)
        svc._preview_cache = _ByteBudgetLRUCache(10_000_000)
        svc._pillow_available = False
        svc._pillow_heif_available = False
        svc._rawpy_available = False

        # Non-existent path — load must fail gracefully
        result = svc.get_thumbnail("/nonexistent/photo.jpg", 128)

        assert isinstance(result, bytes), "Must return bytes even on load failure"
        assert result[:2] == b"\xff\xd8", "Placeholder must be valid JPEG"

    def test_full_res_decode_stays_under_byte_budget(self, tmp_path):
        """N concurrent size=0 (full-res) rawpy decodes are capped at 2 by
        _FULLRES_DECODE_SEM — at most 2 postprocess() calls may run concurrently.

        Real failure mode: without the semaphore, a 60 MP DNG with 4 concurrent
        preview requests allocates ~2.4 GB transiently, triggering OOM on
        systems with 8 GB RAM already partially consumed (#590 class).

        We verify the semaphore constrains concurrency to 2 by instrumenting
        a synthetic rawpy.postprocess() that blocks until released. We launch
        4 threads and assert the peak concurrent acquires never exceeds 2.

        Isolation note: patch("rawpy.imread") is applied ONCE at test scope and
        stopped after all threads finish. Patching inside each thread is unsafe —
        concurrent __enter__/__exit__ calls on the same patcher interleave and
        can leave rawpy.imread as a MagicMock after the test, leaking into
        subsequent tests that call rawpy.imread for real (e.g. test_scan_worker).
        """
        import numpy as np

        # Track peak concurrent acquires
        peak_concurrent = [0]
        current_concurrent = [0]
        lock = threading.Lock()
        barrier = threading.Barrier(4)  # 4 threads, all start together

        def fake_postprocess(**kwargs):
            # Synthetic "heavy decode" that measures concurrency
            with lock:
                current_concurrent[0] += 1
                peak_concurrent[0] = max(peak_concurrent[0], current_concurrent[0])
            try:
                # Small sleep to allow all threads to run inside postprocess
                import time
                time.sleep(0.05)
            finally:
                with lock:
                    current_concurrent[0] -= 1
            # Return a 48 MB synthetic RGB array (400×400×3)
            return np.zeros((400, 400, 3), dtype=np.uint8)

        def _make_rawpy_mock():
            raw = MagicMock()
            raw.extract_thumb.side_effect = svc_mod.LibRawNoThumbnailError("no thumb")
            raw.__enter__ = lambda s: s
            raw.__exit__ = MagicMock(return_value=False)
            raw.postprocess = fake_postprocess
            return raw

        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True
        svc._pillow_available = True
        svc._pillow_heif_available = False

        errors: list[Exception] = []

        def _run_decode():
            try:
                barrier.wait(timeout=5)  # All 4 threads launch together
                # size=0 triggers full-res path + semaphore acquire.
                # rawpy.imread is already patched at test scope (see below).
                result = svc._load_via_rawpy("/fake/photo.dng", 0)
                assert isinstance(result, bytes), f"Must return bytes, got {type(result)}"
            except Exception as ex:
                errors.append(ex)

        # Apply the patch ONCE at the test level, not inside each thread.
        # patch() is NOT thread-safe: concurrent __enter__/__exit__ on the same
        # patcher race each other and can leave rawpy.imread as a MagicMock
        # after the test ends (isolation leak into downstream tests).
        with patch("rawpy.imread", side_effect=lambda path, **kw: _make_rawpy_mock()):
            threads = [threading.Thread(target=_run_decode) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert not errors, f"Decode threads raised: {errors}"
        assert peak_concurrent[0] <= 2, (
            f"_FULLRES_DECODE_SEM should cap concurrent postprocess() at 2, "
            f"but peak was {peak_concurrent[0]}"
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

        pil = PILImage.new("RGB", (width, height), color=(128, 128, 128))
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=85)
        jpeg_bytes = buf.getvalue()

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

    def test_extract_thumb_used_when_large_enough(self):
        """When the embedded JPEG is ≥ viewport_cap in longest side,
        _try_rawpy_embedded_thumb returns bytes and postprocess is
        NOT called on the raw object.

        Real failure mode: always calling postprocess for DNG is ~10×
        slower than using the embedded JPEG; on a 100-DNG scan this
        adds minutes of preview latency.
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True
        svc._pillow_available = True

        raw = self._make_raw_mock_jpeg(4000, 3000)
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=2048)

        assert result is not None, "Should use embedded JPEG when thumb is large enough"
        assert isinstance(result, bytes), "Must return bytes"
        assert result[:2] == b"\xff\xd8", "Must return valid JPEG"
        raw.postprocess.assert_not_called()

    def test_fallback_when_thumb_too_small(self):
        """When the embedded JPEG longest side < viewport_cap,
        _try_rawpy_embedded_thumb returns None so the caller falls
        through to postprocess.

        Real failure mode: using a sub-viewport thumb as the preview
        renders a pixelated/blurry image at native resolution — the bug
        this fast-path was designed to avoid introducing.
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True
        svc._pillow_available = True

        raw = self._make_raw_mock_jpeg(800, 600)
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=2048)

        assert result is None, (
            "Should return None when thumb (800×600) < viewport_cap (2048), "
            "allowing caller to fall through to postprocess"
        )

    def test_fallback_when_no_thumbnail_error(self):
        """When LibRawNoThumbnailError is raised, return None gracefully.

        Real failure mode: propagating the exception would crash the preview
        load for every DNG without an embedded thumbnail (common for older
        camera models).
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True
        svc._pillow_available = True

        raw = self._make_raw_mock_no_thumb()
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=2048)

        assert result is None

    def test_viewport_cap_zero_uses_thumb_regardless_of_size(self):
        """viewport_cap=0 means full-res requested — use the embedded thumb
        regardless of its dimensions (it's the full-res escape hatch for
        the FullResViewerDialog).

        Real failure mode: applying the size check when cap==0 would
        always return None from extract_thumb and always force postprocess
        for the full-res viewer — defeating the fast path entirely.
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True
        svc._pillow_available = True

        raw = self._make_raw_mock_jpeg(400, 300)  # small thumb
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=0)

        assert result is not None, (
            "With viewport_cap=0 (full-res), even a small embedded JPEG should be used"
        )
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8"

    def _make_raw_mock_jpeg_with_orientation(
        self, pixel_width: int, pixel_height: int, orientation: int
    ) -> MagicMock:
        """Build a rawpy mock whose embedded JPEG carries a real EXIF
        Orientation tag. Distinct from ``_make_raw_mock_jpeg`` (which
        writes NO EXIF) — this one uses PIL so ``ImageOps.exif_transpose``
        has something to act on.
        """
        import rawpy as _rawpy

        pil_im = PILImage.new(
            "RGB", (pixel_width, pixel_height), color=(200, 100, 50)
        )
        buf = io.BytesIO()
        exif = pil_im.getexif()
        exif[0x0112] = orientation  # TIFF tag 0x0112 = Orientation
        pil_im.save(buf, format="JPEG", exif=exif.tobytes())

        thumb = SimpleNamespace(
            format=_rawpy.ThumbFormat.JPEG,
            data=buf.getvalue(),
        )
        raw = MagicMock()
        raw.extract_thumb.return_value = thumb
        return raw

    def test_exif_orientation_corrected_for_dng_thumb(self):
        """Embedded JPEG with Orientation=6 (rotate 90 CW, the iPhone
        portrait-grip case) must be transposed so the returned JPEG bytes
        decode to a landscape image (width > height).

        Real failure mode (the bug reported on 2026-06-10): PR #624's
        fast path called ``QImage.loadFromData`` which never reads the
        Orientation tag, so portrait-grip ProRAW DNGs displayed 90°
        rotated relative to Lightroom / File Explorer.

        Post-fix: PIL decode + ``ImageOps.exif_transpose`` swaps the
        dimensions according to the Orientation tag → 4000×3000 → passes.
        """
        svc = ImageService.__new__(ImageService)
        svc._rawpy_available = True
        svc._pillow_available = True

        # Pixels written as 3000×4000 with Orientation=6.
        # After exif_transpose, the image should be 4000×3000 (landscape).
        raw = self._make_raw_mock_jpeg_with_orientation(
            pixel_width=3000, pixel_height=4000, orientation=6
        )
        result = svc._try_rawpy_embedded_thumb(raw, viewport_cap=0)

        assert result is not None and isinstance(result, bytes)
        # Decode the returned JPEG to verify dimensions
        with PILImage.open(io.BytesIO(result)) as decoded:
            assert decoded.width > decoded.height, (
                f"DNG embedded JPEG with Orientation=6 must come out landscape "
                f"after exif_transpose; got {decoded.width}×{decoded.height}"
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
        svc._pillow_available = True
        svc._pillow_heif_available = False
        svc._rawpy_available = False

        jpeg = _make_jpeg(4, 4)

        # Stub _load_from_source to return known bytes
        with patch.object(svc, "_load_from_source") as mock_load:
            mock_load.return_value = jpeg

            # Request a thumbnail (side <= 256 → thumb tier)
            result = svc._get_image("/fake/photo.jpg", 128)

        assert isinstance(result, bytes)
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
        svc._pending_status_msg = None
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


# ── status_reporter wiring (#622 Phase 1) ────────────────────────────────


class TestStatusReporterWiring:
    """The migration-notice plumbing — ``main.py`` builds ImageService
    BEFORE MainWindow's status reporter exists, so the legacy-cache wipe
    message has to be queueable for later delivery. These tests pin both
    paths (synchronous reporter present + deferred reporter-attached-later).
    """

    def test_status_reporter_called_when_reporter_is_set_at_migration_time(
        self, tmp_path
    ):
        """Sync path: a reporter set at construction (or before migration runs)
        is called with the rebuilding-cache message when legacy files exist.

        Failure mode: the original code path that omitted the reporter call
        for the migration notice would leave the user staring at a slower-than-
        normal first launch with no idea that the cache is rebuilding.
        """
        legacy = tmp_path / "old.jpg"
        legacy.write_bytes(b"legacy-thumb")

        reporter = MagicMock()
        svc = ImageService.__new__(ImageService)
        svc._status_reporter = reporter
        svc._pending_status_msg = None
        svc._disk_path = tmp_path

        svc._migrate_legacy_disk_cache()

        reporter.assert_called_once()
        msg_arg = reporter.call_args.args[0]
        assert "cache" in msg_arg.lower(), (
            f"Reporter call must include 'cache' in the message, got: {msg_arg!r}"
        )
        assert svc._pending_status_msg is None, (
            "Pending msg must not be set when reporter consumed it synchronously"
        )

    def test_pending_message_queued_when_no_reporter_at_migration_time(
        self, tmp_path
    ):
        """Deferred path: when reporter is None at migration time, the message
        is queued onto ``_pending_status_msg`` so ``set_status_reporter`` can
        flush it later.

        Failure mode: without the queue, main.py's pre-MainWindow ImageService
        construction silently drops the notice — the user sees no status
        indication of the one-time rebuild.
        """
        legacy = tmp_path / "old.jpg"
        legacy.write_bytes(b"legacy-thumb")

        svc = ImageService.__new__(ImageService)
        svc._status_reporter = None
        svc._pending_status_msg = None
        svc._disk_path = tmp_path

        svc._migrate_legacy_disk_cache()

        assert svc._pending_status_msg is not None, (
            "Migration must queue a pending message when reporter is None"
        )
        assert "cache" in svc._pending_status_msg.lower()

    def test_set_status_reporter_flushes_pending_message(self, tmp_path):
        """``set_status_reporter`` synchronously delivers any queued message.

        Failure mode: a setter that overrode the reporter but failed to
        flush the queue would leave the user without the notice for the
        rest of the session (the migration only fires once).
        """
        svc = ImageService.__new__(ImageService)
        svc._status_reporter = None
        svc._pending_status_msg = "Rebuilding thumbnail cache (version 1)…"

        reporter = MagicMock()
        svc.set_status_reporter(reporter)

        reporter.assert_called_once_with("Rebuilding thumbnail cache (version 1)…")
        assert svc._pending_status_msg is None, (
            "Pending msg must be cleared after flush so a second "
            "set_status_reporter call doesn't re-deliver it"
        )

    def test_set_status_reporter_no_pending_no_call(self, tmp_path):
        """``set_status_reporter`` without a queued message does NOT call the
        reporter — first-install or already-migrated launches must stay quiet.

        Failure mode: an unconditional call would spam the status bar with
        an empty message on every fresh-install launch (no legacy thumbs to
        wipe → no pending msg → reporter should not be called).
        """
        svc = ImageService.__new__(ImageService)
        svc._status_reporter = None
        svc._pending_status_msg = None

        reporter = MagicMock()
        svc.set_status_reporter(reporter)

        reporter.assert_not_called()
        assert svc._status_reporter is reporter, (
            "Reporter must still be attached even when no pending msg existed"
        )

    def test_set_status_reporter_swallows_reporter_exception(self, tmp_path):
        """A reporter that raises during flush must not propagate.

        Failure mode: a fragile status-bar implementation that raises on a
        message containing certain characters could bring down the whole
        MainWindow construction path. The setter's try/except keeps the
        startup robust.
        """
        svc = ImageService.__new__(ImageService)
        svc._status_reporter = None
        svc._pending_status_msg = "ignored"

        def boom(_msg):
            raise RuntimeError("status bar exploded")

        svc.set_status_reporter(boom)  # must not raise
        assert svc._pending_status_msg is None, (
            "Pending msg cleared even on reporter exception "
            "(prevents repeated retries)"
        )

    def test_build_rebuilding_cache_message_uses_translator_when_available(
        self, tmp_path
    ):
        """When ``infrastructure.i18n.t`` resolves the key, the translated
        string wins. Confirms the i18n path is wired.

        Failure mode: a regression where ``_build_rebuilding_cache_message``
        ignored the translator would leave Chinese users seeing English on
        a translated cache-rebuild flow.
        """
        svc = ImageService.__new__(ImageService)
        with patch(
            "infrastructure.i18n.t",
            return_value="TRANSLATED-CACHE-MSG",
        ):
            msg = svc._build_rebuilding_cache_message()
        assert msg == "TRANSLATED-CACHE-MSG"

    def test_build_rebuilding_cache_message_falls_back_on_catalog_miss(
        self, tmp_path
    ):
        """When the translator returns the bare key (catalog miss), the
        fallback English string is returned instead.

        Failure mode: an English-locale user seeing the literal dotted key
        "preview.rebuilding_cache" in the status bar instead of a sentence —
        the canonical translator-miss UX bug class.
        """
        svc = ImageService.__new__(ImageService)
        with patch(
            "infrastructure.i18n.t",
            return_value="preview.rebuilding_cache",
        ):
            msg = svc._build_rebuilding_cache_message()
        assert "Rebuilding thumbnail cache" in msg
        assert PREVIEW_RECIPE_VERSION in msg


# ── Shell/WIC GetImage flag attempt sequence (#734) ──────────────────────


class TestShellGetImageFlagAttempts:
    """Pins the ordered flag-combo sequence _shell_thumbnail_sync tries
    against IShellItemImageFactory::GetImage.

    This is a structural pin, not a live COM test (COM behavior can't be
    faked without test padding — see photo-manager CLAUDE.md). The failure
    mode it guards: a well-meaning refactor silently dropping or reordering
    the bare-RESIZETOFIT rescue would re-break video thumbnails (they'd
    revert to the grey placeholder) with no CI signal, since ubuntu CI has
    no WIC and Windows CI has no guaranteed video codec support — only a
    static assertion on the tuple's shape catches this.
    """

    def test_legacy_two_combos_are_first_in_original_order(self):
        """The two pre-existing flag combos must stay first and in order —
        this is exactly the behavior images already relied on before #734,
        and reordering them would change which attempt succeeds for images
        with a live thumbcache entry.
        """
        legacy_first = (
            _SIIGBF_RESIZETOFIT | _SIIGBF_THUMBNAILONLY | _SIIGBF_BIGGERSIZEOK | _SIIGBF_SCALEUP
        )
        legacy_second = _SIIGBF_RESIZETOFIT | _SIIGBF_BIGGERSIZEOK | _SIIGBF_SCALEUP
        assert _SHELL_GETIMAGE_FLAG_ATTEMPTS[0] == legacy_first
        assert _SHELL_GETIMAGE_FLAG_ATTEMPTS[1] == legacy_second

    def test_bare_resizetofit_rescue_is_last(self):
        """The rescue attempt (flags=0, i.e. bare RESIZETOFIT) must be the
        final attempt in the sequence.

        Failure mode: dropping this attempt, or placing it before the
        legacy combos, silently re-breaks video thumbnails — the Media
        Foundation video provider rejects BIGGERSIZEOK|SCALEUP with
        hr=0x80004005 (E_FAIL) and only succeeds with flags=0.
        """
        assert _SHELL_GETIMAGE_FLAG_ATTEMPTS[-1] == _SIIGBF_RESIZETOFIT
        assert _SHELL_GETIMAGE_FLAG_ATTEMPTS[-1] == 0x00

    def test_exactly_three_attempts_no_extras(self):
        """Pins the sequence length so a stray 4th attempt (or an
        accidental duplicate) doesn't slip in unnoticed.
        """
        assert len(_SHELL_GETIMAGE_FLAG_ATTEMPTS) == 3
