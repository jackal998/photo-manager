"""Image loading, thumbnailing, and caching utilities.

Includes Qt-based decoding, optional Pillow-HEIF support, and Windows Shell/WIC
fallback via ctypes for robust HEIC handling on Windows.
"""

from __future__ import annotations

from collections import OrderedDict
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any
import uuid

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader
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


def _compute_cache_key(path: str, size_key: int) -> str:
    """Compute a stable cache key from path, mtime, size, and requested side."""
    try:
        st = os.stat(path)
        sig = f"{path}|{int(st.st_mtime_ns)}|{int(st.st_size)}|{int(size_key)}".encode(
            "utf-8", errors="ignore"
        )
    except OSError:
        sig = f"{path}|0|0|{int(size_key)}".encode("utf-8", errors="ignore")
    return hashlib.sha1(sig).hexdigest()


def _ensure_dir(p: Path) -> None:
    """Create directory `p` if missing (including parents)."""
    p.mkdir(parents=True, exist_ok=True)


@dataclass
class _MemCacheItem:
    key: str
    image: QImage


class _LRUCache:
    def __init__(self, capacity: int) -> None:
        self._cap = max(1, int(capacity or 1))
        self._data: OrderedDict[str, _MemCacheItem] = OrderedDict()

    def get(self, key: str) -> QImage | None:
        """Return cached QImage for key, moving it to the MRU position."""
        item = self._data.get(key)
        if not item:
            return None
        # move to end (most recent)
        self._data.move_to_end(key)
        return item.image

    def put(self, key: str, image: QImage) -> None:
        """Insert or update `key` with `image`, evicting LRU when over capacity."""
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = _MemCacheItem(key, image)
        else:
            self._data[key] = _MemCacheItem(key, image)
        while len(self._data) > self._cap:
            self._data.popitem(last=False)


class ImageService:
    """High-level image service with memory/disk cache and multiple loaders."""

    def __init__(self, settings: object | None = None) -> None:
        """Initialize caches and optional decoder capabilities from settings."""
        self._mem_cap = 512
        self._disk_dir = str(Path.home() / "AppData" / "Local" / "PhotoManager" / "thumbs")
        if settings is not None:
            try:
                self._mem_cap = int(settings.get("thumbnail_mem_cache", 512) or 512)
            except (ValueError, TypeError):
                self._mem_cap = 512
            raw_dir = settings.get("thumbnail_disk_cache_dir", self._disk_dir)
            if isinstance(raw_dir, str):
                self._disk_dir = os.path.expandvars(raw_dir)
        self._disk_path = Path(self._disk_dir)
        _ensure_dir(self._disk_path)
        self._mem_cache = _LRUCache(self._mem_cap)
        # Optional: Pillow / pillow-heif
        self._pillow_available = bool(PIL_AVAILABLE)
        self._pillow_heif_available = bool(PIL_HEIF_AVAILABLE)

    # Public API
    def get_thumbnail(self, path: str, size: int) -> Any:
        """Return thumbnail image for `path` with max side `size`."""
        return self._get_image(path, size)

    def get_preview(self, path: str, max_side: int) -> Any:
        """Return preview image for `path` bounded by `max_side`."""
        # Preview uses the same pipeline but may request larger size
        return self._get_image(path, max_side)

    # Internal helpers
    def _get_image(self, path: str, requested_side: int) -> QImage:
        """Get image via memory/disk cache or load and cache it."""
        key = _compute_cache_key(path, requested_side)
        img = self._mem_cache.get(key)
        if img is not None and not img.isNull():
            return img

        disk_file = self._disk_path / f"{key}.jpg"
        if disk_file.exists():
            img = QImage(str(disk_file))
            if not img.isNull():
                # Skip obviously invalid cached placeholders
                if self._looks_like_placeholder(img):
                    try:
                        disk_file.unlink()
                    except OSError:
                        pass
                else:
                    self._mem_cache.put(key, img)
                    return img

        # Load from source
        img = self._load_from_source(path, requested_side)
        if img is None or img.isNull():
            # Create placeholder to avoid UI glitches
            img = QImage(64, 64, QImage.Format_ARGB32)
            img.fill(QColor(220, 220, 220))
        else:
            # Save to disk cache (best-effort)
            try:
                img_to_save = img
                # Ensure JPEG-friendly format
                if img.format() == QImage.Format_Invalid:
                    img_to_save = img.convertToFormat(QImage.Format_RGB32)
                img_to_save.save(str(disk_file), "JPEG", quality=85)
            except OSError as ex:
                logger.debug("Save disk cache failed for {}: {}", disk_file, ex)

        self._mem_cache.put(key, img)
        return img

    def _load_from_source(self, path: str, requested_side: int) -> QImage | None:
        """Try Pillow-HEIF, Qt reader, Windows Shell/WIC, then video thumbnail in that order."""
        ext = Path(path).suffix.lower()
        # 0) Prefer Pillow-HEIF for HEIC/HEIF when available
        if ext in {".heic", ".heif"} and self._pillow_available and self._pillow_heif_available:
            img = self._load_via_pillow(path, requested_side)
            if img is not None and not img.isNull():
                return img
            # If Pillow-HEIF failed, try Windows Shell/WIC as fallback for HEIC
            try:
                return self._load_via_shell_thumbnail(path, requested_side)
            except OSError as ex:
                logger.debug("Shell/WIC thumbnail failed for HEIC {}: {}", path, ex)
                return None

        # 1) Try QImageReader for common formats (non-HEIC)
        try:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            if requested_side and requested_side > 0 and reader.size().isValid():
                orig = reader.size()
                w, h = orig.width(), orig.height()
                if w > 0 and h > 0:
                    if w >= h:
                        nw = min(requested_side, w)
                        nh = int(h * (nw / max(1, w)))
                    else:
                        nh = min(requested_side, h)
                        nw = int(w * (nh / max(1, h)))
                    reader.setScaledSize(QSize(nw, nh))
            img = reader.read()
            if img is not None and not img.isNull():
                # Ensure bounded scaling if reader scaling not applied
                if requested_side and requested_side > 0:
                    img = img.scaled(
                        requested_side, requested_side, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                return img
        except (OSError, ValueError) as ex:
            logger.debug("QImageReader failed for {}: {}", path, ex)

        # 2) Fallback: Windows Shell/WIC thumbnail (good on Windows, incl. many videos)
        try:
            return self._load_via_shell_thumbnail(path, requested_side)
        except OSError as ex:
            logger.debug("Shell/WIC thumbnail failed for {}: {}", path, ex)
            return None

    def _load_via_pillow(self, path: str, requested_side: int) -> QImage | None:
        """Load image with Pillow (HEIF supported if pillow-heif is registered)."""
        if not self._pillow_available:
            return None
        try:
            assert Image is not None and ImageOps is not None  # for type checkers
            with Image.open(path) as im:  # pillow-heif registers opener for HEIC
                try:
                    im = ImageOps.exif_transpose(im)
                except (OSError, ValueError, AttributeError):
                    pass
                if requested_side and requested_side > 0:
                    resampling = getattr(Image, "Resampling", Image)
                    resample = getattr(resampling, "LANCZOS", getattr(resampling, "BICUBIC", 3))
                    im.thumbnail((requested_side, requested_side), resample)
                return self._pil_to_qimage(im)
        except (OSError, ValueError) as ex:
            logger.debug("Pillow load failed for {}: {}", path, ex)
            return None

    def _pil_to_qimage(self, pil_img: Any) -> QImage | None:
        """Convert a Pillow image to `QImage` and detach from the source buffer."""
        try:
            # Convert Pillow image to QImage efficiently
            mode = pil_img.mode
            if mode not in ("RGBA", "RGB"):
                pil_img = pil_img.convert("RGBA")
                mode = pil_img.mode
            if mode == "RGB":
                data = pil_img.tobytes("raw", "RGB")
                qimg = QImage(
                    data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format_RGB888
                )
            else:
                data = pil_img.tobytes("raw", "RGBA")
                qimg = QImage(
                    data, pil_img.width, pil_img.height, pil_img.width * 4, QImage.Format_RGBA8888
                )
            if qimg is None or qimg.isNull():
                return None
            return qimg.copy()
        except (ValueError, TypeError) as ex:
            logger.debug("PIL->QImage convert failed: {}", ex)
            return None

    # Windows Shell/WIC via ctypes, return QImage or None
    def _load_via_shell_thumbnail(self, path: str, side: int) -> QImage | None:
        """Windows Shell/WIC thumbnail provider via ctypes.

        Returns a `QImage` or None on failure.
        """
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
            # SIIGBF flags
            siigbf_resizetofit = 0x00
            siigbf_biggersizeok = 0x01
            siigbf_thumbnailonly = 0x08
            siigbf_scaleup = 0x10

            # SHCreateItemFromParsingName
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            gdi32 = ctypes.windll.gdi32

            ole32.CoInitialize(None)
            try:
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

                def _try_get_image(request_px: int) -> QImage | None:
                    size = SIZE(request_px, request_px)
                    hbm_local = wintypes.HBITMAP()
                    flags = (
                        siigbf_resizetofit
                        | siigbf_thumbnailonly
                        | siigbf_biggersizeok
                        | siigbf_scaleup
                    )
                    hr_local = get_image_fn(ppsi, size, flags, ctypes.byref(hbm_local))
                    if hr_local != 0 or not hbm_local:
                        # Fallback attempt without THUMBNAILONLY
                        hbm_local = wintypes.HBITMAP()
                        flags2 = siigbf_resizetofit | siigbf_biggersizeok | siigbf_scaleup
                        hr2 = get_image_fn(ppsi, size, flags2, ctypes.byref(hbm_local))
                        if hr2 != 0 or not hbm_local:
                            return None

                    # Convert HBITMAP to QImage
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

                        qi = QImage(bytes(buf), width, height, row_bytes, QImage.Format_ARGB32)
                        if qi.isNull():
                            return None
                        img_local = qi.convertToFormat(QImage.Format_RGB32)
                        return img_local.copy()
                    finally:
                        if hdc_local:
                            gdi32.DeleteDC(hdc_local)
                        if hbm_local:
                            gdi32.DeleteObject(hbm_local)

                for candidate in size_candidates:
                    img_result = _try_get_image(candidate)
                    if img_result is not None and not img_result.isNull():
                        try:
                            release_fn(ppsi)
                        except OSError:
                            pass
                        return img_result

                try:
                    release_fn(ppsi)
                except OSError:
                    pass
                return None
            finally:
                ole32.CoUninitialize()
        except OSError as ex:
            logger.debug("_load_via_shell_thumbnail exception: {}", ex)
            return None

    # Note: No video thumbnail extraction via QMediaPlayer in background threads.
    # For videos, rely on Windows Shell/WIC where available; otherwise, fallback to
    # placeholder and update thumbnail when the tile starts playing.

    def _looks_like_placeholder(self, img: QImage) -> bool:
        """Heuristic to detect grey placeholder images."""
        try:
            if img.width() == 64 and img.height() == 64:
                c1 = QColor(img.pixel(0, 0))
                c2 = QColor(img.pixel(img.width() // 2, img.height() // 2))
                c3 = QColor(img.pixel(img.width() - 1, img.height() - 1))

                def is_grey(c: QColor) -> bool:
                    return (
                        abs(c.red() - 220) <= 2
                        and abs(c.green() - 220) <= 2
                        and abs(c.blue() - 220) <= 2
                    )

                return is_grey(c1) and is_grey(c2) and is_grey(c3)
        except (ValueError, TypeError):
            return False
        return False
