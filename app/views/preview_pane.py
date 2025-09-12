from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from loguru import logger

from app.views.constants import (
    DEFAULT_THUMB_SIZE,
    GRID_MARGIN_RATIO,
    GRID_MIN_THUMB_PX,
    GRID_SPACING_PX,
)
from app.views.image_tasks import ImageTaskRunner


class PreviewPane(QWidget):
    """Encapsulates the right-side preview (single image / grid)."""

    def __init__(
        self, parent: QWidget | None, task_runner: ImageTaskRunner, thumb_size: int | None = None
    ) -> None:
        super().__init__(parent)
        self._runner = task_runner
        self._thumb_size = int(thumb_size or DEFAULT_THUMB_SIZE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(QLabel("Preview"))

        self.preview_area = QScrollArea()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.preview_area.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self._preview_container = QWidget()
        self._preview_layout = QVBoxLayout(self._preview_container)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)

        self._single_label = QLabel("(preview)")
        self._single_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._single_label.setMinimumHeight(200)
        self._preview_layout.addWidget(self._single_label)

        self.preview_area.setWidget(self._preview_container)
        root.addWidget(self.preview_area)

        # state
        self._current_single_token: str | None = None
        self._grid_labels: dict[str, QLabel] = {}
        self._grid_container: QWidget | None = None
        self._grid_layout: QGridLayout | None = None
        self._grid_items: list[tuple[str, str, str, str]] = []
        self._single_pm: QPixmap | None = None

        # Track preview viewport resizes to keep fit-on-width accurate
        try:
            self.preview_area.viewport().installEventFilter(self)
            self.preview_area.installEventFilter(self)
        except Exception:
            pass

    # Public API
    def show_single(self, path: str) -> None:
        self.clear()
        self.preview_area.setWidgetResizable(False)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._single_label.setVisible(True)
        self._single_label.setText("Loading…")
        self._current_single_token = self._runner.request_single_preview(path)

    def show_grid(self, items: list[tuple[str, str, str, str]]) -> None:
        self.clear()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._grid_items = list(items)

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(GRID_SPACING_PX)
        self._apply_grid_margins()
        cols, thumb_side = self._compute_grid_geometry()
        self._grid_labels = {}
        for i, it in enumerate(items):
            p, name, folder, size_txt = it
            r, c = divmod(i, cols)
            tile = QWidget()
            v = QVBoxLayout(tile)
            v.setContentsMargins(0, 0, 0, 0)
            img_lbl = QLabel("Loading…")
            img_lbl.setFixedSize(thumb_side, thumb_side)
            img_lbl.setAlignment(Qt.AlignCenter)
            v.addWidget(img_lbl)
            info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes")
            info.setWordWrap(True)
            v.addWidget(info)
            self._grid_layout.addWidget(tile, r, c)
            token = self._runner.request_grid_thumbnail(p, thumb_side)
            self._grid_labels[token] = img_lbl
        self._preview_layout.addWidget(self._grid_container)

    def clear(self) -> None:
        if self._grid_container is not None:
            self._preview_layout.removeWidget(self._grid_container)
            self._grid_container.deleteLater()
            self._grid_container = None
            self._grid_layout = None
        self._grid_labels.clear()
        self._grid_items = []
        self._single_label.clear()
        self._single_label.setVisible(False)
        self._single_pm = None

    def on_image_loaded(self, token: str, path: str, image: Any) -> None:
        try:
            if token.startswith("single|"):
                if token != self._current_single_token:
                    return
                if image is None:
                    self._single_label.setText("(failed)")
                    return
                pm = QPixmap.fromImage(image)
                if pm.isNull():
                    self._single_label.setText("(failed)")
                    return
                self._single_pm = pm
                self._apply_single_pixmap_fit()
                self._single_label.setText("")
            elif token.startswith("grid|"):
                lbl = self._grid_labels.get(token)
                if not lbl or image is None:
                    return
                pm = QPixmap.fromImage(image)
                if pm.isNull():
                    lbl.setText("(failed)")
                    return
                lbl.setPixmap(
                    pm.scaled(
                        lbl.width(), lbl.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
                lbl.setText("")
        except Exception as ex:  # pragma: no cover - UI best effort
            logger.error("Update preview failed: {}", ex)

    def refit(self) -> None:
        """Public hook to re-apply single-image fit (e.g., on splitterMoved)."""
        self._apply_single_pixmap_fit()

    # Qt events
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._grid_container is not None and self._grid_layout is not None and self._grid_items:
            # rebuild grid with new geometry
            self._preview_layout.removeWidget(self._grid_container)
            self._grid_container.deleteLater()
            self._grid_container = QWidget()
            self._grid_layout = QGridLayout(self._grid_container)
            self._grid_layout.setSpacing(GRID_SPACING_PX)
            self._apply_grid_margins()
            cols, thumb_side = self._compute_grid_geometry()
            self._grid_labels = {}
            for i, it in enumerate(self._grid_items):
                p, name, folder, size_txt = it
                r, c = divmod(i, cols)
                tile = QWidget()
                v = QVBoxLayout(tile)
                v.setContentsMargins(0, 0, 0, 0)
                img_lbl = QLabel("Loading…")
                img_lbl.setFixedSize(thumb_side, thumb_side)
                img_lbl.setAlignment(Qt.AlignCenter)
                v.addWidget(img_lbl)
                info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes")
                info.setWordWrap(True)
                v.addWidget(info)
                self._grid_layout.addWidget(tile, r, c)
                token = self._runner.request_grid_thumbnail(p, thumb_side)
                self._grid_labels[token] = img_lbl
            self._preview_layout.addWidget(self._grid_container)
        else:
            self._apply_single_pixmap_fit()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        try:
            if event and event.type() == QEvent.Resize:
                if obj is self.preview_area or obj is self.preview_area.viewport():
                    self._apply_single_pixmap_fit()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    # internals
    def _apply_grid_margins(self) -> None:
        if self._grid_layout is None:
            return
        vp = self.preview_area.viewport()
        w = max(1, vp.width())
        h = max(1, vp.height())
        m_lr = int(w * GRID_MARGIN_RATIO)
        m_tb = int(h * GRID_MARGIN_RATIO)
        self._grid_layout.setContentsMargins(m_lr, m_tb, m_lr, m_tb)

    def _compute_grid_geometry(self) -> tuple[int, int]:
        viewport = self.preview_area.viewport()
        width = max(1, viewport.width())
        spacing = GRID_SPACING_PX
        min_px = GRID_MIN_THUMB_PX
        try:
            max_px = int(self._thumb_size) if int(self._thumb_size) > 0 else 600
        except Exception:
            max_px = 600
        best_cols = 1
        best_cell = min_px
        for cols in range(1, 64):
            total_spacing = spacing * (cols - 1)
            cell = (width - total_spacing) // cols
            if cell < min_px:
                break
            cand = min(cell, max_px)
            if cand >= best_cell:
                best_cell = cand
                best_cols = cols
        return best_cols, best_cell

    def _apply_single_pixmap_fit(self) -> None:
        try:
            if self._grid_container is not None and self._grid_items:
                return
            if self._single_pm is None or self._single_pm.isNull():
                return
            vp = self.preview_area.viewport()
            max_w = max(1, vp.width())
            pm = self._single_pm
            target_w = min(pm.width(), max_w - 1)
            if target_w <= 0:
                target_w = pm.width()
            if pm.width() != target_w:
                scaled = pm.scaledToWidth(target_w, Qt.SmoothTransformation)
                self._single_label.setPixmap(scaled)
            else:
                self._single_label.setPixmap(pm)
            self._single_label.adjustSize()
            try:
                self._preview_container.adjustSize()
            except Exception:
                pass
        except Exception:
            pass
