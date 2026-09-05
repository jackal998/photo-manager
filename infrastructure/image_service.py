"""Image loading, thumbnailing, and caching utilities.

Includes Pillow-based decoding, optional rawpy support for DNG, and Windows
Shell/WIC fallback via ctypes for robust HEIC handling on Windows.

Qt-free: returns JPEG bytes. The only GUI-side conversion is in
``app/views/image_tasks._bytes_to_qimage`` which wraps bytes for signal delivery.
"""

from __future__ import annotations

import atexit
from collections import OrderedDict
import ctypes
import sys
from dataclasses import dataclass
import hashlib
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
import uuid

# wintypes is a Windows-only ctypes module; guard so the module imports on Linux
# (the web layer is the cross-platform consumer even though CI is Windows-only today).
if sys.platform == "win32":
    from ctypes import wintypes  # type: ignore[attr-defined]

from loguru import logger

# Optional Pillow and HEIF support (top-level to satisfy linting)
try:  # pragma: no cover - import availability
    from PIL import Image, ImageOps  # type: ignore

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PIL_AVAILABLE = False
    Image = None  # type: ignore
    ImageOps = None  # type: ignore

try:  # pragma: no cover - optional dependency
    from pillow_heif import register_heif_opener  # type: ignore

    register_heif_opener()
    PIL_HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PIL_HEIF_AVAILABLE = False

try:  # pragma: no cover - optional dependency
    import rawpy  # type: ignore
    from rawpy import LibRawNoThumbnailError  # type: ignore  # noqa: F401

    RAWPY_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    RAWPY_AVAILABLE = False
    LibRawNoThumbnailError = Exception  # type: ignore


# Recipe version — bump to invalidate the disk cache namespace.
PREVIEW_RECIPE_VERSION = "1"

# Size boundary (longest side) separating "thumb" vs "preview" tier at put time.
_THUMB_SIDE_THRESHOLD = 256

# Default sizes for the two cache tiers (overridden at init from RAM probe).
_THUMB_CACHE_DEFAULT_BYTES = 64 * 1024 * 1024   # 64 MB
_PREVIEW_CACHE_DEFAULT_BYTES = 192 * 1024 * 1024  # 192 MB

# Full-res OOM semaphore: caps concurrent rawpy.postprocess() calls (each may
# transiently allocate 300–800 MB for 60 MP ProRAW). Static value 2 gives an
# ~1.6 GB ceiling. Phase 1 may expose this as a setting; Phase 0 keeps it fixed.
_FULLRES_DECODE_SEM = threading.BoundedSemaphore(2)

# Module-level 64×64 grey placeholder JPEG, generated once at import.
# Used when source load fails so downstream callers always get valid bytes.
def _make_placeholder_jpeg() -> bytes:
    """Return a 64×64 grey JPEG (220, 220, 220) generated via Pillow."""
    try:
        assert Image is not None
        pil = Image.new("RGB", (64, 64), color=(220, 220, 220))
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # pragma: no cover - PIL always available in production
        # Minimal valid 1×1 JPEG as last-resort fallback
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
            b'\xc3\x80\x01\x01\xff\xd9'
        )

_PLACEHOLDER_JPEG: bytes = _make_placeholder_jpeg()


# ── IShellItemImageFactory::GetImage flags (shellapi.h SIIGBF_*) ──────────
_SIIGBF_RESIZETOFIT = 0x00
_SIIGBF_BIGGERSIZEOK = 0x01
_SIIGBF_THUMBNAILONLY = 0x08
_SIIGBF_SCALEUP = 0x10

# Ordered GetImage flag combos to try per requested size, thumbcache-hit
# fast path first, ending with a bare-RESIZETOFIT rescue. The rescue exists
# because the Media Foundation video thumbnail provider (no Explorer
# thumbcache entry) rejects BIGGERSIZEOK|SCALEUP with hr=0x80004005 (E_FAIL)
# but succeeds with flags=0 — measured against a VP9-in-MP4 sample and a
# 139 MB NAS HEVC .MOV, both returning real decoded frames. Attempt 1 fails
# with hr=0x80030002 (STG_E_FILENOTFOUND) when there is no thumbcache entry.
# Images still resolve at attempt 1 or 2; the rescue is unreached for them.
_SHELL_GETIMAGE_FLAG_ATTEMPTS: tuple[int, ...] = (
    _SIIGBF_RESIZETOFIT | _SIIGBF_THUMBNAILONLY | _SIIGBF_BIGGERSIZEOK | _SIIGBF_SCALEUP,
    _SIIGBF_RESIZETOFIT | _SIIGBF_BIGGERSIZEOK | _SIIGBF_SCALEUP,
    _SIIGBF_RESIZETOFIT,
)


# ── Windows COM STA executor for WIC/Shell calls ──────────────────────────
#
# WIC COM objects must be called from a thread that has called
# CoInitializeEx(None, COINIT_APARTMENTTHREADED=0x2). Creating a dedicated
# ThreadPoolExecutor with an STA initializer satisfies this requirement
# without per-call CoInitialize/CoUninitialize churn.
#
# Phase 1 replaces this atexit drain with a FastAPI lifespan hook.
# The executor and drain are Windows-only; on Linux/macOS the Shell/WIC
# path is never reached so a no-op executor keeps the module importable.

if sys.platform == "win32":
    def _sta_init() -> None:
        """STA initializer for the WIC executor thread pool."""
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        except Exception:  # pragma: no cover - already initialised
            pass

    _wic_executor: ThreadPoolExecutor = ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="wic-sta",
        initializer=_sta_init,
    )

    def _drain_wic_executor() -> None:
        """Drain the WIC executor on shutdown, calling CoUninitialize on each worker."""
        try:
            def _coinit_ex() -> None:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:  # pragma: no cover
                    pass

            futures = [_wic_executor.submit(_coinit_ex) for _ in range(2)]
            for f in futures:
                try:
                    f.result(timeout=5)
                except Exception:  # pragma: no cover
                    pass
            _wic_executor.shutdown(wait=False)
        except Exception:  # pragma: no cover
            pass

else:  # pragma: no cover - Linux/macOS; WIC path never reached
    def _sta_init() -> None:  # type: ignore[misc]
        pass

    _wic_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wic-noop")

    def _drain_wic_executor() -> None:  # type: ignore[misc]
        _wic_executor.shutdown(wait=False)


atexit.register(_drain_wic_executor)


def _probe_total_ram() -> int | None:
    """Return total physical RAM in bytes, or None on any failure.

    The real-OS code paths are excluded from unit-test coverage — ctypes.windll
    is unavailable on Linux CI; exercised by monkeypatching this function in
    unit tests and by Windows local runs.
    """
    import sys

    if sys.platform == "win32":
        return _probe_total_ram_windows()
    return _probe_total_ram_posix()


def _probe_total_ram_windows() -> int | None:  # pragma: no cover
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys)
    except Exception:
        return None


def _probe_total_ram_posix() -> int | None:  # pragma: no cover
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        if page > 0 and pages > 0:
            return int(page) * int(pages)
    except Exception:
        pass
    return None


def _compute_cache_budgets() -> tuple[int, int]:
    """Return (thumb_bytes, preview_bytes) based on available RAM.

    Budget = min(256 MB, total_RAM // 32), split 64 MB thumb / 192 MB preview.
    Falls back to defaults on probe failure.
    """
    total = _probe_total_ram()
    if total and total > 0:
        combined = min(256 * 1024 * 1024, total // 32)
    else:
        combined = _THUMB_CACHE_DEFAULT_BYTES + _PREVIEW_CACHE_DEFAULT_BYTES
    # Split: 25% thumb, 75% preview, but at least 16 MB each.
    thumb = max(16 * 1024 * 1024, combined // 4)
    preview = max(16 * 1024 * 1024, combined - thumb)
    return thumb, preview


def _compute_cache_key(path: str, size_key: int, source_mtime_ns: int = 0) -> str:
    """Compute a stable cache key from path, requested side, and source mtime.

    Folding ``source_mtime_ns`` in means an in-place edit to the source file
    (same path + size, different content — e.g. a crop/re-export) produces a
    fresh key instead of silently hitting the old cache entry forever
    (Correction #11). Defaults to 0 so any caller that can't cheaply supply
    a source mtime still gets a stable, deterministic key.
    """
    sig = f"{path}|{int(size_key)}|{int(source_mtime_ns)}".encode("utf-8", errors="ignore")
    return hashlib.sha1(sig).hexdigest()


def _stat_mtime_ns(path: str) -> int:
    """Best-effort source-file mtime_ns; 0 if the stat fails (e.g. deleted)."""
    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return 0


# How long one path's mtime may be reused before the source is stat'd again.
# 5 s is the #622 Phase 2 figure: long enough that a burst of requests for the
# same file (rapid clicks, a grid tile plus the single view, an ETag lookup
# beside its own decode) costs ONE stat, short enough that a user who edits a
# photo in another application sees the new version almost immediately.
_MTIME_TTL_SECONDS = 5.0

# Hard ceiling on how many paths the mtime cache may hold. Entries are only
# useful for _MTIME_TTL_SECONDS, but nothing re-visits a path once the user
# has moved on, so without a cap the map would grow by one entry per distinct
# file previewed and never shrink for the life of the process. 512 is far more
# than any one grid or burst needs (a grid shows a few dozen files), so the
# cap never evicts an entry that is still live in practice.
_MTIME_CACHE_MAX_ENTRIES = 512


class _MtimeStatCache:
    """Per-path TTL cache over the source-file mtime stat (#622 Phase 2).

    Every preview request folds ``source_mtime_ns`` into its cache key, which
    means every request stats the source — and on a NAS that stat is a network
    round trip, paid even on a memory-cache hit. Requests arrive in bursts for
    the same path, so a short TTL collapses the burst to one stat.

    The TTL is deliberately NOT "cache the mtime forever": a path-only key
    would never notice an external edit (the user re-exports a photo over the
    same filename on the NAS) and the pane would serve the pre-edit pixels
    indefinitely. After the TTL the source is stat'd again, the new mtime
    produces a different cache key, and the edit shows up.

    A failed stat (0) is cached like any other value: a deleted file is
    re-checked once the TTL lapses.

    **Bounded.** An entry stops being useful after the TTL, but nothing comes
    back to evict it — a session that browses N distinct files would otherwise
    hold N entries until the process exits. Inserts therefore sweep expired
    entries once the map reaches ``max_entries``, and fall back to
    oldest-first eviction if a burst is genuinely wider than the cap. Size is
    bounded by ``max_entries``, not by how long the app has been running.

    Thread-safe — decode workers call this from several QThreadPool threads.
    ``clock``, ``stat_fn`` and ``max_entries`` are injected so the TTL and the
    eviction behaviour are testable without sleeping, without allocating the
    production cap, and without monkeypatching the standard library.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = _MTIME_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        stat_fn: Callable[[str], int] = _stat_mtime_ns,
        max_entries: int = _MTIME_CACHE_MAX_ENTRIES,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._stat = stat_fn
        self._max_entries = max(1, int(max_entries))
        self._entries: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> int:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(path)
            if entry is not None and (now - entry[0]) < self._ttl:
                return entry[1]
        # Stat outside the lock: it is the slow network call, and holding the
        # lock across it would serialise every path behind the slowest one.
        value = self._stat(path)
        with self._lock:
            self._evict_locked(now)
            self._entries[path] = (now, value)
        return value

    def _evict_locked(self, now: float) -> None:
        """Keep the map under ``max_entries``. Caller holds the lock.

        Cheap in the common case: it does nothing at all until the cap is
        reached, and the sweep it then runs is O(size) once per cap-worth of
        new paths, not per request.
        """
        if len(self._entries) < self._max_entries:
            return
        # Normally the whole map is stale by now — entries live 5 s and the
        # cap takes far longer than that to fill.
        for stale in [
            p for p, (stamp, _) in self._entries.items() if (now - stamp) >= self._ttl
        ]:
            del self._entries[stale]
        # A burst wider than the cap within one TTL: drop oldest-first. dicts
        # keep insertion order, so the first key is the oldest inserted.
        while len(self._entries) >= self._max_entries:
            del self._entries[next(iter(self._entries))]

    def clear(self) -> None:
        """Drop every entry.

        Test-isolation hook (an autouse fixture in ``tests/conftest.py`` calls
        it between tests so one test's ``tmp_path`` stat cannot answer the
        next one's). Production relies on the TTL and on ``_evict_locked``,
        not on anyone calling this.
        """
        with self._lock:
            self._entries.clear()


_mtime_cache = _MtimeStatCache()


def _source_mtime_ns(path: str) -> int:
    """Source-file mtime_ns, TTL-cached per path (see :class:`_MtimeStatCache`)."""
    return _mtime_cache.get(path)


def _ensure_dir(p: Path) -> None:
    """Create directory `p` if missing (including parents)."""
    p.mkdir(parents=True, exist_ok=True)


@dataclass
class _MemCacheItem:
    key: str
    data: bytes
    byte_size: int


class _ByteBudgetLRUCache:
    """LRU cache with a byte-budget capacity instead of item-count capacity.

    Evicts the LRU entry whenever the total byte sum would exceed the budget.

    Thread-safe. ``_ImageTask`` (``app/views/image_tasks.py``) calls
    ``get``/``put`` from QThreadPool workers; ``clear()`` runs on the
    main thread on manifest unload. Without the lock the OrderedDict
    mutation in any of the three would race the others (#616).
    """

    def __init__(self, budget_bytes: int) -> None:
        self._budget = max(1, int(budget_bytes))
        self._data: OrderedDict[str, _MemCacheItem] = OrderedDict()
        self._total_bytes: int = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            self._data.move_to_end(key)
            return item.data

    def put(self, key: str, data: bytes) -> None:
        byte_size = len(data) if data else 1
        with self._lock:
            if key in self._data:
                old = self._data[key]
                self._total_bytes -= old.byte_size
                self._data.move_to_end(key)
                self._data[key] = _MemCacheItem(key, data, byte_size)
                self._total_bytes += byte_size
            else:
                self._data[key] = _MemCacheItem(key, data, byte_size)
                self._total_bytes += byte_size
            # Evict LRU until within budget
            while self._total_bytes > self._budget and len(self._data) > 1:
                _, evicted = self._data.popitem(last=False)
                self._total_bytes -= evicted.byte_size

    def clear(self) -> None:
        """Evict all entries and reset the byte counter (#616)."""
        with self._lock:
            self._data.clear()
            self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        return self._total_bytes


class ImageService:
    """High-level image service with memory/disk cache and multiple loaders."""

    def __init__(self, settings: object | None = None, status_reporter: Any | None = None) -> None:
        """Initialize caches and optional decoder capabilities from settings."""
        self._status_reporter = status_reporter
        # Queue for migration-time messages produced before a reporter is
        # attached. Production constructs ImageService in main.py BEFORE
        # MainWindow's StatusReporterImpl exists, so the legacy-cache wipe
        # has no one to talk to at __init__ time. ``set_status_reporter``
        # flushes this queue when called.
        self._pending_status_msg: str | None = None
        self._disk_dir = str(
            Path.home() / "AppData" / "Local" / "PhotoManager" / "thumbs"
        )
        if settings is not None:
            raw_dir = settings.get("thumbnail_disk_cache_dir", self._disk_dir)
            if isinstance(raw_dir, str):
                self._disk_dir = os.path.expandvars(raw_dir)
        self._disk_path = Path(self._disk_dir)
        # Versioned sub-directory for the disk cache
        self._versioned_disk_path = self._disk_path / f"v{PREVIEW_RECIPE_VERSION}"
        _ensure_dir(self._versioned_disk_path)

        # Migrate legacy thumbs: wipe any *.jpg files directly under thumbs/
        # (i.e., NOT under v1/ or any version sub-dir) on first launch.
        self._migrate_legacy_disk_cache()

        # Byte-budget caches
        thumb_bytes, preview_bytes = _compute_cache_budgets()
        self._thumb_cache = _ByteBudgetLRUCache(thumb_bytes)
        self._preview_cache = _ByteBudgetLRUCache(preview_bytes)

        # Optional: Pillow / pillow-heif
        self._pillow_available = bool(PIL_AVAILABLE)
        self._pillow_heif_available = bool(PIL_HEIF_AVAILABLE)
        # Optional: rawpy for DNG
        self._rawpy_available = bool(RAWPY_AVAILABLE)

    def _migrate_legacy_disk_cache(self) -> None:
        """Wipe legacy (non-versioned) .jpg files from the thumbs root.

        Files under versioned sub-directories (v1/, v2/, …) are left intact.
        Fires once at init; the versioned sub-dir is already created above so
        the wildcard glob only matches root-level files.
        """
        try:
            legacy_jpgs = [
                f
                for f in self._disk_path.glob("*.jpg")
                if f.is_file()
            ]
            if not legacy_jpgs:
                return
            for f in legacy_jpgs:
                try:
                    f.unlink()
                except OSError:
                    pass
            msg = self._build_rebuilding_cache_message()
            logger.info(msg)
            if self._status_reporter is not None:
                try:
                    self._status_reporter(msg)
                except Exception:
                    pass
            else:
                # Production case: reporter not wired yet (main.py builds
                # ImageService before MainWindow). Queue for ``set_status_reporter``.
                self._pending_status_msg = msg
        except Exception as ex:
            logger.debug("Legacy disk cache migration failed: {}", ex)

    def _build_rebuilding_cache_message(self) -> str:
        """Return the legacy-cache-wipe status string, i18n if available.

        Lazy-imports the translator so the disk-cache migration also runs
        cleanly in unit tests that construct ImageService before
        ``init_translator``. Falls back to the original English string when
        the translator returns the bare key (catalog miss) or when the i18n
        module itself can't be imported.
        """
        fallback = (
            "Rebuilding thumbnail cache "
            f"(version {PREVIEW_RECIPE_VERSION}) — this is a one-time operation."
        )
        try:
            from infrastructure.i18n import t  # lazy import — see docstring
            translated = t("preview.rebuilding_cache", version=PREVIEW_RECIPE_VERSION)
            if not translated or translated == "preview.rebuilding_cache":
                return fallback
            return translated
        except Exception:
            return fallback

    def set_status_reporter(self, reporter: Any) -> None:
        """Attach a status reporter post-construction.

        Required because ``main.py`` builds the ImageService BEFORE the
        MainWindow's ``StatusReporterImpl`` exists, so any "rebuilding cache"
        message produced during ``_migrate_legacy_disk_cache`` would otherwise
        be silently swallowed (logger-only). The queued message is flushed
        synchronously here — the reporter's ``showMessage`` is allowed to be
        called before ``QMainWindow.show()`` (Qt queues the text until the
        status bar is realised).
        """
        self._status_reporter = reporter
        pending = self._pending_status_msg
        if pending is not None and reporter is not None:
            try:
                reporter(pending)
            except Exception:
                pass
            finally:
                self._pending_status_msg = None

    # Public API
    def get_thumbnail(self, path: str, size: int) -> bytes:
        """Return JPEG bytes for thumbnail of `path` with max side `size`."""
        return self._get_image(path, size)

    def get_preview(self, path: str, max_side: int) -> bytes:
        """Return JPEG bytes for preview of `path` bounded by `max_side`."""
        return self._get_image(path, max_side)

    def clear_cache(self) -> None:
        """Drop all in-memory cached images (thumb + preview tiers).

        Called when a manifest unloads so RAM from the previous library
        is released before the next manifest's thumbnails begin to
        populate (#616). The disk cache under the versioned thumbs
        directory is intentionally preserved — it's keyed by content
        hash and valid across manifests.
        """
        self._thumb_cache.clear()
        self._preview_cache.clear()

    # Web API entry points (Phase 1)

    def get_image_bytes(
        self,
        path: str,
        size: int,
        quality: int = 85,
        source_mtime_ns: int | None = None,
    ) -> bytes:
        """Return JPEG bytes for the web image endpoint.

        Thin wrapper over ``_get_image`` — reuses the existing mem/disk cache,
        the _FULLRES_DECODE_SEM (acquired inside _load_via_rawpy for size==0),
        and the _wic_executor STA dispatch.  No second semaphore or executor.

        ``size == 0`` → full-res (passes requested_side=0 to _get_image which
        falls through to rawpy.postprocess or Shell/WIC at max WIC size).
        ``size > 0``  → thumbnail/preview tier (same as get_thumbnail/get_preview).

        ``source_mtime_ns``: pass a pre-stat'd value (the web route already
        stats ``resolved`` for path validation) to avoid a second stat of the
        same source file; ``None`` computes it internally.
        """
        return self._get_image(path, size, source_mtime_ns)

    def image_disk_cache_path(
        self, path: str, size: int, source_mtime_ns: int | None = None
    ) -> Path:
        """Return the disk-cache file path for (path, size, source mtime).

        Reuses the same versioned key as ``_get_image`` so the route can
        stat() it for an mtime-bearing ETag without re-deriving the key.
        Returns the path regardless of whether the file exists yet.

        Pass the SAME ``source_mtime_ns`` used for the matching
        ``get_image_bytes`` call so both derive the identical key from one
        source stat (Correction #11 perf note: at most one extra os.stat per
        request) — ``None`` re-stats internally for callers that don't have
        it handy.
        """
        if source_mtime_ns is None:
            source_mtime_ns = _source_mtime_ns(path)
        key = _compute_cache_key(path, size, source_mtime_ns)
        return self._versioned_disk_path / f"{key}.jpg"

    # Internal helpers
    def _get_image(
        self,
        path: str,
        requested_side: int,
        source_mtime_ns: int | None = None,
    ) -> bytes:
        """Get image via memory/disk cache or load and cache it.

        ``source_mtime_ns`` folds the source file's mtime into the cache key
        (Correction #11) so overwriting a photo in place (same path + size)
        gets a fresh key instead of serving stale cached bytes indefinitely.
        ``None`` (the default, used by get_thumbnail/get_preview) stats the
        source itself; the web route passes a pre-stat'd value.
        """
        if source_mtime_ns is None:
            source_mtime_ns = _source_mtime_ns(path)
        key = _compute_cache_key(path, requested_side, source_mtime_ns)

        # Determine which cache tier to consult (thumb vs preview)
        cache = self._thumb_cache if requested_side > 0 and requested_side <= _THUMB_SIDE_THRESHOLD else self._preview_cache

        cached = cache.get(key)
        if cached is not None:
            return cached

        disk_file = self._versioned_disk_path / f"{key}.jpg"
        if disk_file.exists():
            try:
                jpeg = disk_file.read_bytes()
                if jpeg and not self._looks_like_placeholder(jpeg):
                    cache.put(key, jpeg)
                    return jpeg
                else:
                    try:
                        disk_file.unlink()
                    except OSError:
                        pass
            except OSError:
                pass

        # Load from source
        jpeg = self._load_from_source(path, requested_side)
        if not jpeg:
            jpeg = _PLACEHOLDER_JPEG
        else:
            try:
                disk_file.write_bytes(jpeg)
            except OSError as ex:
                logger.debug("Save disk cache failed for {}: {}", disk_file, ex)

        cache.put(key, jpeg)
        return jpeg

    # pylint: disable=too-many-return-statements,too-many-branches
    def _load_from_source(self, path: str, requested_side: int) -> bytes | None:
        """Try Pillow-HEIF, then Pillow generic or Windows Shell/WIC as applicable."""
        ext = Path(path).suffix.lower()
        # 0) Prefer Pillow-HEIF for HEIC/HEIF when available
        if ext in {".heic", ".heif"} and self._pillow_available and self._pillow_heif_available:
            jpeg = self._load_via_pillow(path, requested_side)
            if jpeg:
                return jpeg
            try:
                return self._load_via_shell_thumbnail(path, requested_side)
            except OSError as ex:
                logger.debug("Shell/WIC thumbnail failed for HEIC {}: {}", path, ex)
                return None

        # 1) Explicit DNG handling: rawpy (embedded JPEG fast path) -> Pillow -> Shell/WIC
        if ext == ".dng":
            if self._rawpy_available:
                try:
                    jpeg = self._load_via_rawpy(path, requested_side)
                    if jpeg:
                        return jpeg
                except (OSError, ValueError) as ex:
                    logger.debug("rawpy load failed for DNG {}: {}", path, ex)

            jpeg = self._load_via_pillow_generic(path, requested_side)
            if jpeg:
                return jpeg

            try:
                return self._load_via_shell_thumbnail(path, requested_side)
            except OSError as ex:
                logger.debug("Shell/WIC thumbnail failed for DNG {}: {}", path, ex)
                return None

        # 2) Other still images: Try Pillow generic
        jpeg = self._load_via_pillow_generic(path, requested_side)
        if jpeg:
            return jpeg

        # 3) Fallback: Windows Shell/WIC thumbnail
        try:
            return self._load_via_shell_thumbnail(path, requested_side)
        except OSError as ex:
            logger.debug("Shell/WIC thumbnail failed for {}: {}", path, ex)
            return None

    def _load_via_pillow_generic(self, path: str, requested_side: int) -> bytes | None:
        """Load any image with Pillow, return JPEG bytes or None on failure."""
        if not self._pillow_available:
            return None
        try:
            assert Image is not None and ImageOps is not None
            with Image.open(path) as im:
                try:
                    im = ImageOps.exif_transpose(im)
                except (OSError, ValueError, AttributeError):
                    pass
                if requested_side and requested_side > 0:
                    resampling = getattr(Image, "Resampling", Image)
                    resample = getattr(resampling, "LANCZOS", getattr(resampling, "BICUBIC", 3))
                    im.thumbnail((requested_side, requested_side), resample)
                out = io.BytesIO()
                im.convert("RGB").save(out, "JPEG", quality=85)
                return out.getvalue()
        except (OSError, ValueError) as ex:
            logger.debug("Pillow generic load failed for {}: {}", path, ex)
            return None

    def _load_via_pillow(self, path: str, requested_side: int) -> bytes | None:
        """Load image with Pillow (HEIF supported if pillow-heif is registered).

        Returns JPEG bytes or None on failure.
        """
        return self._load_via_pillow_generic(path, requested_side)

    def _load_via_rawpy(
        self, path: str, requested_side: int
    ) -> bytes | None:  # pylint: disable=W0718
        """Load DNG via rawpy, returning JPEG bytes.

        Tries embedded JPEG first (fast path): if extract_thumb() returns a
        thumb whose longest side >= requested_side (or requested_side == 0),
        returns it directly. Falls through to raw.postprocess() when the
        embedded thumb is too small or when LibRawNoThumbnailError is raised.

        Full-res decode (requested_side == 0) acquires _FULLRES_DECODE_SEM
        before calling postprocess() to cap the ~300-800 MB transient RAM peak.
        """
        if not self._rawpy_available:
            return None
        try:
            assert RAWPY_AVAILABLE and rawpy is not None  # type: ignore[name-defined]
            with rawpy.imread(path) as raw:  # type: ignore[attr-defined]
                # Fast path: try embedded JPEG thumbnail
                thumb_jpeg = self._try_rawpy_embedded_thumb(raw, requested_side)
                if thumb_jpeg is not None:
                    return thumb_jpeg

                # Full decode fallback — gate concurrent full-res decodes
                full_res = requested_side == 0
                if full_res:
                    _FULLRES_DECODE_SEM.acquire()
                try:
                    rgb = raw.postprocess(  # pylint: disable=no-member
                        use_auto_wb=True,
                        no_auto_bright=False,
                        auto_bright_thr=0.01,
                        bright=1.0,
                        output_color=rawpy.ColorSpace.sRGB,  # pylint: disable=no-member
                        output_bps=8,
                        gamma=(2.222, 4.5),
                        demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,  # pylint: disable=no-member
                        highlight_mode=rawpy.HighlightMode.Blend,  # pylint: disable=no-member
                    )
                finally:
                    if full_res:
                        _FULLRES_DECODE_SEM.release()

            assert Image is not None
            pil = Image.fromarray(rgb, "RGB")  # type: ignore[attr-defined]
            if requested_side and requested_side > 0:
                resampling = getattr(Image, "Resampling", Image)
                resample = getattr(resampling, "LANCZOS", getattr(resampling, "BICUBIC", 3))
                pil.thumbnail((requested_side, requested_side), resample)
            out = io.BytesIO()
            quality = 95 if requested_side == 0 else 85
            pil.save(out, "JPEG", quality=quality)
            return out.getvalue()
        except Exception as ex:  # pragma: no cover - optional path  # pylint: disable=W0718
            logger.debug("rawpy exception for {}: {}", path, ex)
            return None

    def _try_rawpy_embedded_thumb(self, raw: Any, viewport_cap: int) -> bytes | None:
        """Extract the embedded JPEG thumbnail from a rawpy raw file object.

        Returns JPEG bytes if the thumb is large enough (longest side >= viewport_cap,
        or viewport_cap == 0 meaning full-res is requested). Returns None when:
        - LibRawNoThumbnailError is raised (no embedded thumb),
        - the thumb is too small,
        - or any other extraction error occurs.
        """
        try:
            thumb = raw.extract_thumb()  # type: ignore[attr-defined]
            import rawpy as _rawpy  # type: ignore[attr-defined]
            if thumb.format == _rawpy.ThumbFormat.JPEG:  # type: ignore[attr-defined]
                # JPEG bytes → decode via Pillow so EXIF Orientation is honoured.
                # Raw byte loading via Qt's loadFromData ignores embedded JPEG Orientation
                # tags (auto-rotate only fires via the reader API); Pillow + exif_transpose
                # gives correct orientation for portrait-grip ProRAW DNGs.
                assert Image is not None and ImageOps is not None
                with Image.open(io.BytesIO(bytes(thumb.data))) as pil_im:
                    try:
                        pil_im = ImageOps.exif_transpose(pil_im)
                    except (OSError, ValueError, AttributeError):
                        # exif_transpose only raises on malformed EXIF —
                        # fall through to the un-rotated image rather than
                        # failing the whole load.
                        pass
                    # Check size before committing
                    if viewport_cap > 0:
                        longest = max(pil_im.width, pil_im.height)
                        if longest < viewport_cap:
                            return None  # too small — fall through to postprocess

                    if viewport_cap > 0:
                        resampling = getattr(Image, "Resampling", Image)
                        resample = getattr(resampling, "LANCZOS", getattr(resampling, "BICUBIC", 3))
                        pil_im.thumbnail((viewport_cap, viewport_cap), resample)
                    out = io.BytesIO()
                    pil_im.convert("RGB").save(out, "JPEG", quality=85)
                    return out.getvalue()
            else:
                # Bitmap array (H, W, 3)
                arr = thumb.data
                assert Image is not None
                pil = Image.fromarray(arr, "RGB")

                if viewport_cap > 0:
                    longest = max(pil.width, pil.height)
                    if longest < viewport_cap:
                        return None  # too small — fall through to postprocess
                    resampling = getattr(Image, "Resampling", Image)
                    resample = getattr(resampling, "LANCZOS", getattr(resampling, "BICUBIC", 3))
                    pil.thumbnail((viewport_cap, viewport_cap), resample)

                out = io.BytesIO()
                pil.convert("RGB").save(out, "JPEG", quality=85)
                return out.getvalue()
        except LibRawNoThumbnailError:
            return None
        except Exception as ex:
            logger.debug("rawpy extract_thumb failed: {}", ex)
            return None

    # Windows Shell/WIC via ctypes, returns JPEG bytes or None
    def _load_via_shell_thumbnail(self, path: str, side: int) -> bytes | None:
        """Windows Shell/WIC thumbnail provider via ctypes.

        Runs synchronously on the calling thread via the module-level STA
        executor so WIC COM objects are always called from an STA-initialised
        thread. Returns JPEG bytes or None on failure.
        """
        fut = _wic_executor.submit(self._shell_thumbnail_sync, path, side)
        try:
            return fut.result(timeout=10)
        except Exception as ex:
            logger.debug("_load_via_shell_thumbnail future failed for {}: {}", path, ex)
            return None

    def _shell_thumbnail_sync(self, path: str, side: int) -> bytes | None:
        """WIC/Shell thumbnail extraction — must run on an STA-initialised thread.

        Called exclusively through _wic_executor so the STA guarantee holds.
        """
        assert threading.current_thread().name.startswith("wic-sta"), (
            f"WIC COM called off-STA thread: {threading.current_thread().name} "
            f"(RPC_E_WRONG_THREAD risk — use _wic_executor)"
        )
        try:

            class SIZE(ctypes.Structure):
                """C SIZE struct."""

                _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

            # GUID helper
            class GUID(ctypes.Structure):
                """GUID struct for COM interop."""

                _fields_ = [
                    ("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

                def __init__(self, guid_str: str) -> None:  # type: ignore[override]
                    ctypes.Structure.__init__(self)
                    u = uuid.UUID(guid_str)
                    self.Data1 = u.time_low
                    self.Data2 = u.time_mid
                    self.Data3 = u.time_hi_version
                    last_eight = list(u.bytes[8:])
                    self.Data4[:] = (ctypes.c_ubyte * 8)(*last_eight)

            iid_ishell_item_image_factory = GUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")

            # SHCreateItemFromParsingName
            shell32 = ctypes.windll.shell32
            gdi32 = ctypes.windll.gdi32

            sh_create_item_from_parsing_name = shell32.SHCreateItemFromParsingName
            sh_create_item_from_parsing_name.argtypes = [
                wintypes.LPCWSTR,
                ctypes.c_void_p,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            sh_create_item_from_parsing_name.restype = ctypes.c_long

            ppsi = ctypes.c_void_p()
            hr = sh_create_item_from_parsing_name(
                path, None, ctypes.byref(iid_ishell_item_image_factory), ctypes.byref(ppsi)
            )
            if hr != 0:
                return None

            # Define vtable for IShellItemImageFactory::GetImage
            class IShellItemImageFactory(ctypes.Structure):
                """Partial vtable with single pointer to vtable structure."""

                _fields_ = [("lpVtbl", ctypes.POINTER(ctypes.c_void_p))]

            # vtable layout: QueryInterface, AddRef, Release, GetImage
            get_image_proto = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_void_p,
                SIZE,
                ctypes.c_uint,
                ctypes.POINTER(wintypes.HBITMAP),
            )
            release_proto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)

            factory = IShellItemImageFactory.from_address(ppsi.value)
            vtbl = ctypes.cast(factory.lpVtbl, ctypes.POINTER(ctypes.c_void_p * 4)).contents
            get_image_fn = get_image_proto(vtbl[3])
            release_fn = release_proto(vtbl[2])

            # Normalize to stable WIC request sizes (512 or 1024) and try both if needed
            requested = side if side and side > 0 else 1024
            size_candidates = [512, 1024] if requested <= 512 else [1024, 512]

            def _try_get_image(request_px: int) -> bytes | None:
                size = SIZE(request_px, request_px)
                hbm_local = wintypes.HBITMAP()
                for flags in _SHELL_GETIMAGE_FLAG_ATTEMPTS:
                    hbm_local = wintypes.HBITMAP()
                    hr_local = get_image_fn(ppsi, size, flags, ctypes.byref(hbm_local))
                    if hr_local == 0 and hbm_local:
                        break
                else:
                    return None

                # Convert HBITMAP to PIL Image → JPEG bytes
                class BITMAPINFOHEADER(ctypes.Structure):
                    """Bitmap header structure."""

                    _fields_ = [
                        ("biSize", ctypes.c_uint32),
                        ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long),
                        ("biPlanes", ctypes.c_ushort),
                        ("biBitCount", ctypes.c_ushort),
                        ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32),
                        ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32),
                    ]

                class BITMAPINFO(ctypes.Structure):
                    """Bitmap info structure."""

                    _fields_ = [
                        ("bmiHeader", BITMAPINFOHEADER),
                        ("bmiColors", ctypes.c_uint32 * 3),
                    ]

                bi_rgb = 0
                dib_rgb_colors = 0

                class BITMAP(ctypes.Structure):
                    """Bitmap structure."""

                    _fields_ = [
                        ("bmType", ctypes.c_long),
                        ("bmWidth", ctypes.c_long),
                        ("bmHeight", ctypes.c_long),
                        ("bmWidthBytes", ctypes.c_long),
                        ("bmPlanes", ctypes.c_ushort),
                        ("bmBitsPixel", ctypes.c_ushort),
                        ("bmBits", ctypes.c_void_p),
                    ]

                bmp = BITMAP()
                gdi32.GetObjectW(hbm_local, ctypes.sizeof(BITMAP), ctypes.byref(bmp))
                width, height = int(bmp.bmWidth), int(bmp.bmHeight)
                if width <= 0 or height <= 0:
                    gdi32.DeleteObject(hbm_local)
                    return None

                bmi = BITMAPINFO()
                ctypes.memset(ctypes.byref(bmi), 0, ctypes.sizeof(bmi))
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = width
                bmi.bmiHeader.biHeight = -height  # top-down
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = bi_rgb

                hdc_local = gdi32.CreateCompatibleDC(None)
                try:
                    row_bytes = ((width * 32 + 31) // 32) * 4
                    buf_size = row_bytes * height
                    buf = (ctypes.c_ubyte * buf_size)()
                    res_local = gdi32.GetDIBits(
                        hdc_local,
                        hbm_local,
                        0,
                        height,
                        ctypes.byref(buf),
                        ctypes.byref(bmi),
                        dib_rgb_colors,
                    )
                    if res_local == 0:
                        return None

                    # BGRA buffer → PIL → JPEG bytes
                    assert Image is not None
                    pil = Image.frombytes("RGBA", (width, height), bytes(buf), "raw", "BGRA")
                    pil = pil.convert("RGB")
                    out = io.BytesIO()
                    pil.save(out, "JPEG", quality=85)
                    return out.getvalue()
                finally:
                    if hdc_local:
                        gdi32.DeleteDC(hdc_local)
                    if hbm_local:
                        gdi32.DeleteObject(hbm_local)

            for candidate in size_candidates:
                jpeg_result = _try_get_image(candidate)
                if jpeg_result:
                    try:
                        release_fn(ppsi)
                    except OSError:
                        pass
                    return jpeg_result

            try:
                release_fn(ppsi)
            except OSError:
                pass
            return None
        except OSError as ex:
            logger.debug("_shell_thumbnail_sync exception: {}", ex)
            return None

    def _looks_like_placeholder(self, jpeg: bytes) -> bool:
        """Heuristic to detect grey placeholder images.

        Fast path: byte-identical match against the module-level placeholder.
        Fallback: open via Pillow and verify 64×64 with pixels near (220, 220, 220).
        """
        if jpeg == _PLACEHOLDER_JPEG:
            return True
        try:
            assert Image is not None
            with Image.open(io.BytesIO(jpeg)) as im:
                if im.width != 64 or im.height != 64:
                    return False
                im_rgb = im.convert("RGB")
                for xy in ((0, 0), (32, 32), (63, 63)):
                    r, g, b = im_rgb.getpixel(xy)
                    if not (abs(r - 220) <= 5 and abs(g - 220) <= 5 and abs(b - 220) <= 5):
                        return False
                return True
        except (ValueError, TypeError, OSError):
            return False
