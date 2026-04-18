from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from loguru import logger

# Register pillow-heif once for faster subsequent opens (if available)
try:
    from pillow_heif import register_heif_opener  # type: ignore

    register_heif_opener()
except Exception:
    pass


def _pil_to_qimage(pil_img) -> QImage | None:
    try:
        # Lazily import to keep dependency optional

        if pil_img.mode not in ("RGBA", "RGB"):
            pil_img = pil_img.convert("RGBA")
        if pil_img.mode == "RGB":
            # QImage.Format_RGB888 is fine; ensure bytes alignment
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
        # Detach from Python buffer
        return qimg.copy()
    except Exception as ex:
        logger.error("PIL->QImage failed: {}", ex)
        return None


def load_pillow_heif(path: str, side: int = 0) -> tuple[QImage | None, str | None]:
    """
    Decode HEIC via Pillow + pillow-heif (if installed). Returns QImage.
    pip install pillow pillow-heif
    """
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as im:
            # Apply EXIF orientation
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass

            if side and side > 0:
                # Preserve aspect ratio
                try:
                    resample = getattr(Image, "Resampling", Image).LANCZOS  # Pillow>=9 compat
                except Exception:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                im.thumbnail((side, side), resample)

            qimg = _pil_to_qimage(im)
            if qimg is None or qimg.isNull():
                return None, "Pillow conversion produced null image"
            return qimg, None
    except ImportError as ie:
        return None, f"Pillow/pillow-heif not available: {ie}"
    except Exception as ex:
        logger.error("Pillow-HEIF load failed: {}", ex)
        return None, str(ex)


def main() -> int:
    app = QApplication(sys.argv)

    files = sys.argv[1:] or [
        r"h:\\photos\\mobilebackup\\iphone\\2023\\01\\img_3302.heic",
        r"h:\\photos\\mobilebackup\\iphone\\2023\\11\\img_8388.heic",
    ]

    win = QWidget()
    root = QVBoxLayout(win)

    for p in files:
        root.addWidget(QLabel(f"Testing: {p}"))
        row = QHBoxLayout()

        def _make_loader(path: str, side: int):
            return lambda: load_pillow_heif(path, side)

        for method_name, loader in (
            ("Pillow-HEIF 512", _make_loader(p, 512)),
            ("Pillow-HEIF 1024", _make_loader(p, 1024)),
        ):
            v = QVBoxLayout()
            v.addWidget(QLabel(method_name))
            img, err = loader()
            lbl = QLabel(err or "(ok)")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedSize(400, 300)
            if img is not None and not img.isNull():
                pm = QPixmap.fromImage(img)
                if not pm.isNull():
                    lbl.setPixmap(
                        pm.scaled(
                            lbl.width(), lbl.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                        )
                    )
                    lbl.setText("")
            v.addWidget(lbl)
            row.addLayout(v)

        root.addLayout(row)

    win.resize(1200, 900)
    win.setWindowTitle("HEIC Load Test")
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
