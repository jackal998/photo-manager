"""Full-resolution image viewer dialog (#622 Phase 1).

Opens a non-modal window showing the full raw-decoded image with pan/zoom.
The image is loaded asynchronously via ImageService and is NOT stored in the
byte-budget LRU — the dialog owns its QImage reference and releases it on close.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from loguru import logger


class _ZoomLabel(QLabel):
    """QLabel that supports pan via mouse drag and Ctrl+wheel zoom.

    Emits scale changes by calling the provided `on_scale_changed` callback.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_start: QPoint | None = None
        self._scroll_area: QScrollArea | None = None

    def attach_scroll_area(self, sa: QScrollArea) -> None:
        self._scroll_area = sa

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_start is not None and self._scroll_area is not None:
            delta = event.globalPosition().toPoint() - self._drag_start
            self._drag_start = event.globalPosition().toPoint()
            hbar = self._scroll_area.horizontalScrollBar()
            vbar = self._scroll_area.verticalScrollBar()
            if hbar:
                hbar.setValue(hbar.value() - delta.x())
            if vbar:
                vbar.setValue(vbar.value() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)


class FullResViewerDialog(QDialog):
    """Non-modal full-resolution viewer with pan/zoom.

    Image load is performed synchronously in the constructor (on the calling
    thread) — this dialog is opened from the UI thread via a double-click so
    it inherits Qt's normal event-delivery context. Large images may cause a
    brief pause; async loading via ImageTaskRunner is a Phase 2 concern.
    """

    def __init__(
        self,
        path: str,
        parent: QWidget | None = None,
        *,
        service: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        # Optional dependency-injected ImageService. Production wires the
        # app-level instance via ``main_window.on_open_full_res_viewer`` so
        # the dialog reuses the same disk cache, byte-budget LRU, and
        # status-reporter wiring. Falls back to constructing a bare instance
        # when None — the test path patches the class symbol and exercises
        # both branches (see ``tests/test_dialogs/test_full_res_viewer.py``).
        self._service: Any | None = service
        self._full_qimage: Any = None  # QImage | None
        self._current_scale: float = 1.0

        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(900, 700)

        filename = Path(path).name
        self.setWindowTitle(filename)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)

        self._label = _ZoomLabel()
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setCursor(Qt.OpenHandCursor)
        self._label.attach_scroll_area(self._scroll)

        self._scroll.setWidget(self._label)
        layout.addWidget(self._scroll)

        # Load the image (bypasses the byte-budget LRU — full res)
        self._load_image()

    def _load_image(self) -> None:
        """Load the full-resolution image synchronously."""
        try:
            if self._service is not None:
                svc = self._service
            else:
                # Fallback: standalone construction (no DI). Triggers an
                # extra ``_migrate_legacy_disk_cache`` pass on first open,
                # which is idempotent once the v1/ sub-dir is populated.
                from infrastructure.image_service import ImageService
                svc = ImageService()
            # side=0 → full resolution (bypass viewport cap)
            img = svc.get_preview(self._path, 0)
            if img is None or img.isNull():
                self._label.setText(f"Could not load:\n{os.path.basename(self._path)}")
                return
            self._full_qimage = img
            pm = QPixmap.fromImage(img)
            self._current_scale = 1.0
            self._label.setPixmap(pm)
            self._label.adjustSize()
            # Update title with resolution
            self.setWindowTitle(
                f"{Path(self._path).name}  [{img.width()}×{img.height()}]"
            )
        except Exception as ex:
            logger.warning("FullResViewerDialog load failed for {}: {}", self._path, ex)
            self._label.setText(f"Load failed:\n{os.path.basename(self._path)}\n{ex}")

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        """Ctrl+wheel → zoom in/out; plain wheel → scroll (default)."""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._apply_zoom(1.25)
            elif delta < 0:
                self._apply_zoom(0.8)
            event.accept()
        else:
            super().wheelEvent(event)

    def _apply_zoom(self, factor: float) -> None:
        """Scale the displayed pixmap by `factor` relative to current scale."""
        if self._full_qimage is None or self._full_qimage.isNull():
            return
        new_scale = max(0.05, min(8.0, self._current_scale * factor))
        self._current_scale = new_scale
        src_w = self._full_qimage.width()
        src_h = self._full_qimage.height()
        target_w = max(1, int(src_w * new_scale))
        target_h = max(1, int(src_h * new_scale))
        scaled = QPixmap.fromImage(
            self._full_qimage.scaled(
                QSize(target_w, target_h), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
        self._label.setPixmap(scaled)
        self._label.adjustSize()

    def closeEvent(self, event: Any) -> None:  # type: ignore[override]
        """Release the full-res QImage on close to free memory."""
        self._full_qimage = None
        super().closeEvent(event)

    def keyPressEvent(self, event: Any) -> None:  # type: ignore[override]
        """Esc closes the dialog (default QDialog behaviour preserved)."""
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
