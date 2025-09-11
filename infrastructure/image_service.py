from __future__ import annotations

from typing import Any, Optional
import hashlib
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PySide6.QtGui import QImage, QImageReader, QColor
from PySide6.QtCore import QSize, Qt

 


def _compute_cache_key(path: str, size_key: int) -> str:
    try:
        st = os.stat(path)
        sig = f"{path}|{int(st.st_mtime_ns)}|{int(st.st_size)}|{int(size_key)}".encode("utf-8", errors="ignore")
    except Exception:
        sig = f"{path}|0|0|{int(size_key)}".encode("utf-8", errors="ignore")
    return hashlib.sha1(sig).hexdigest()


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


@dataclass
class _MemCacheItem:
    key: str
    image: QImage


class _LRUCache:
    def __init__(self, capacity: int) -> None:
        self._cap = max(1, int(capacity or 1))
        self._data: OrderedDict[str, _MemCacheItem] = OrderedDict()

    def get(self, key: str) -> Optional[QImage]:
        item = self._data.get(key)
        if not item:
            return None
        # move to end (most recent)
        self._data.move_to_end(key)
        return item.image

    def put(self, key: str, image: QImage) -> None:
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = _MemCacheItem(key, image)
        else:
            self._data[key] = _MemCacheItem(key, image)
        while len(self._data) > self._cap:
            self._data.popitem(last=False)


class ImageService:
    def __init__(self, settings: Optional[object] = None) -> None:
        self._mem_cap = 512
        self._disk_dir = str(Path.home() / "AppData" / "Local" / "PhotoManager" / "thumbs")
        if settings is not None:
            try:
                self._mem_cap = int(settings.get("thumbnail_mem_cache", 512) or 512)
            except Exception:
                self._mem_cap = 512
            try:
                raw_dir = settings.get("thumbnail_disk_cache_dir", self._disk_dir)
                if isinstance(raw_dir, str):
                    self._disk_dir = os.path.expandvars(raw_dir)
            except Exception:
                pass
        self._disk_path = Path(self._disk_dir)
        _ensure_dir(self._disk_path)
        self._mem_cache = _LRUCache(self._mem_cap)
        # Optional: Pillow / pillow-heif
        self._pillow_available = False
        self._pillow_heif_available = False
        try:
            import PIL  # type: ignore

            self._pillow_available = True
            try:
                from pillow_heif import register_heif_opener  # type: ignore

                register_heif_opener()
                self._pillow_heif_available = True
            except Exception:
                self._pillow_heif_available = False
        except Exception:
            self._pillow_available = False

    # Public API
    def get_thumbnail(self, path: str, size: int) -> Any:
        return self._get_image(path, size)

    def get_preview(self, path: str, max_side: int) -> Any:
        # Preview uses the same pipeline but may request larger size
        return self._get_image(path, max_side)

    # Internal helpers
    def _get_image(self, path: str, requested_side: int) -> QImage:
        key = _compute_cache_key(path, requested_side)
        img = self._mem_cache.get(key)
        if img is not None and not img.isNull():
            return img

        disk_file = self._disk_path / f"{key}.jpg"
        if disk_file.exists():
            try:
                img = QImage(str(disk_file))
                if not img.isNull():
                    # Skip obviously invalid cached placeholders
                    if self._looks_like_placeholder(img):
                        try:
                            disk_file.unlink()
                        except Exception:
                            pass
                    else:
                        self._mem_cache.put(key, img)
                        return img
            except Exception:
                pass

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
            except Exception as ex:
                logger.debug("Save disk cache failed for {}: {}", disk_file, ex)

        self._mem_cache.put(key, img)
        return img

    def _load_from_source(self, path: str, requested_side: int) -> Optional[QImage]:
        ext = Path(path).suffix.lower()
        # 0) Prefer Pillow-HEIF for HEIC/HEIF when available
        if ext in {".heic", ".heif"} and self._pillow_available:
            img = self._load_via_pillow(path, requested_side)
            if img is not None and not img.isNull():
                return img

        # 1) Try QImageReader for common formats
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
                    img = img.scaled(requested_side, requested_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                return img
        except Exception as ex:
            logger.debug("QImageReader failed for {}: {}", path, ex)

        # 2) Fallback: Windows Shell/WIC thumbnail (good on Windows, esp. HEIC)
        try:
            return self._load_via_shell_thumbnail(path, requested_side)
        except Exception as ex:
            logger.debug("Shell/WIC thumbnail failed for {}: {}", path, ex)
            return None

    def _load_via_pillow(self, path: str, requested_side: int) -> Optional[QImage]:
        if not self._pillow_available:
            return None
        try:
            from PIL import Image, ImageOps  # type: ignore

            with Image.open(path) as im:  # pillow-heif registers opener for HEIC
                try:
                    im = ImageOps.exif_transpose(im)
                except Exception:
                    pass
                if requested_side and requested_side > 0:
                    try:
                        resample = getattr(Image, "Resampling", Image).LANCZOS  # Pillow>=9 compat
                    except Exception:
                        resample = Image.LANCZOS  # type: ignore[attr-defined]
                    im.thumbnail((requested_side, requested_side), resample)
                return self._pil_to_qimage(im)
        except Exception as ex:
            logger.debug("Pillow load failed for {}: {}", path, ex)
            return None

    def _pil_to_qimage(self, pil_img: Any) -> Optional[QImage]:
        try:
            # Convert Pillow image to QImage efficiently
            mode = pil_img.mode
            if mode not in ("RGBA", "RGB"):
                pil_img = pil_img.convert("RGBA")
                mode = pil_img.mode
            if mode == "RGB":
                data = pil_img.tobytes("raw", "RGB")
                qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format_RGB888)
            else:
                data = pil_img.tobytes("raw", "RGBA")
                qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 4, QImage.Format_RGBA8888)
            if qimg is None or qimg.isNull():
                return None
            return qimg.copy()
        except Exception as ex:
            logger.debug("PIL->QImage convert failed: {}", ex)
            return None

    # Windows Shell/WIC via ctypes, return QImage or None
    def _load_via_shell_thumbnail(self, path: str, side: int) -> Optional[QImage]:
        try:
            import ctypes
            from ctypes import wintypes

            CLSCTX_INPROC_SERVER = 0x1

            class SIZE(ctypes.Structure):
                _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

            # GUID helper
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

                def __init__(self, guid_str: str) -> None:  # type: ignore[override]
                    ctypes.Structure.__init__(self)
                    import uuid as _uuid

                    u = _uuid.UUID(guid_str)
                    self.Data1 = u.time_low
                    self.Data2 = u.time_mid
                    self.Data3 = u.time_hi_version
                    data4 = list(u.bytes[8:])
                    self.Data4[:] = (ctypes.c_ubyte * 8)(*data4)

            IID_IShellItemImageFactory = GUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")
            # SIIGBF flags
            SIIGBF_RESIZETOFIT = 0x00
            SIIGBF_BIGGERSIZEOK = 0x01
            SIIGBF_MEMORYONLY = 0x02
            SIIGBF_ICONONLY = 0x04
            SIIGBF_THUMBNAILONLY = 0x08
            SIIGBF_SCALEUP = 0x10

            # SHCreateItemFromParsingName
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            gdi32 = ctypes.windll.gdi32
            user32 = ctypes.windll.user32

            ole32.CoInitialize(None)
            try:
                SHCreateItemFromParsingName = shell32.SHCreateItemFromParsingName
                SHCreateItemFromParsingName.argtypes = [
                    wintypes.LPCWSTR,
                    ctypes.c_void_p,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                ]
                SHCreateItemFromParsingName.restype = ctypes.c_long

                ppsi = ctypes.c_void_p()
                hr = SHCreateItemFromParsingName(path, None, ctypes.byref(IID_IShellItemImageFactory), ctypes.byref(ppsi))
                if hr != 0:
                    return None

                # Define vtable for IShellItemImageFactory::GetImage
                class IShellItemImageFactory(ctypes.Structure):
                    pass

                # vtable layout: QueryInterface, AddRef, Release, GetImage
                _GetImageProto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE, ctypes.c_uint, ctypes.POINTER(wintypes.HBITMAP))
                _ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)

                IShellItemImageFactory._fields_ = [("lpVtbl", ctypes.POINTER(ctypes.c_void_p))]

                factory = IShellItemImageFactory.from_address(ppsi.value)
                vtbl = ctypes.cast(factory.lpVtbl, ctypes.POINTER(ctypes.c_void_p * 4)).contents
                GetImage = _GetImageProto(vtbl[3])
                Release = _ReleaseProto(vtbl[2])

                # Normalize to stable WIC request sizes (512 or 1024) and try both if needed
                requested = side if side and side > 0 else 1024
                size_candidates = [512, 1024] if requested <= 512 else [1024, 512]

                def _try_get_image(request_px: int) -> Optional[QImage]:
                    size = SIZE(request_px, request_px)
                    hbm_local = wintypes.HBITMAP()
                    flags = SIIGBF_RESIZETOFIT | SIIGBF_THUMBNAILONLY | SIIGBF_BIGGERSIZEOK | SIIGBF_SCALEUP
                    hr_local = GetImage(ppsi, size, flags, ctypes.byref(hbm_local))
                    if hr_local != 0 or not hbm_local:
                        # Fallback attempt without THUMBNAILONLY
                        hbm_local = wintypes.HBITMAP()
                        flags2 = SIIGBF_RESIZETOFIT | SIIGBF_BIGGERSIZEOK | SIIGBF_SCALEUP
                        hr2 = GetImage(ppsi, size, flags2, ctypes.byref(hbm_local))
                        if hr2 != 0 or not hbm_local:
                            return None

                    # Convert HBITMAP to QImage
                    class BITMAPINFOHEADER(ctypes.Structure):
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
                        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

                    BI_RGB = 0
                    DIB_RGB_COLORS = 0

                    class BITMAP(ctypes.Structure):
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
                    bmi.bmiHeader.biCompression = BI_RGB

                    hdc_local = gdi32.CreateCompatibleDC(None)
                    try:
                        row_bytes = ((width * 32 + 31) // 32) * 4
                        buf_size = row_bytes * height
                        buf = (ctypes.c_ubyte * buf_size)()
                        res_local = gdi32.GetDIBits(hdc_local, hbm_local, 0, height, ctypes.byref(buf), ctypes.byref(bmi), DIB_RGB_COLORS)
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
                            Release(ppsi)
                        except Exception:
                            pass
                        return img_result

                try:
                    Release(ppsi)
                except Exception:
                    pass
                return None
            finally:
                ole32.CoUninitialize()
        except Exception as ex:
            logger.debug("_load_via_shell_thumbnail exception: {}", ex)
            return None

    def _looks_like_placeholder(self, img: QImage) -> bool:
        try:
            if img.width() == 64 and img.height() == 64:
                c1 = QColor(img.pixel(0, 0))
                c2 = QColor(img.pixel(img.width() // 2, img.height() // 2))
                c3 = QColor(img.pixel(img.width() - 1, img.height() - 1))
                def is_grey(c: QColor) -> bool:
                    return abs(c.red() - 220) <= 2 and abs(c.green() - 220) <= 2 and abs(c.blue() - 220) <= 2
                return is_grey(c1) and is_grey(c2) and is_grey(c3)
        except Exception:
            return False
        return False

