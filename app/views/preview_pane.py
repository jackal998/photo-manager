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
from app.views.media_utils import is_video, normalize_windows_path
from app.views.widgets.group_media_controller import GroupMediaController
from app.views.widgets.video_player import VideoPlayerWidget


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

        # Video support
        self._single_video_player: VideoPlayerWidget | None = None
        self._grid_video_players: dict[str, VideoPlayerWidget] = {}
        self._grid_media_controller: GroupMediaController | None = None
        self._grid_pending_video_labels: dict[str, QLabel] = {}
        self._grid_all_players_ready: bool = False

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

        if is_video(path):
            # Show video player
            try:
                # For video, allow container to resize with viewport
                self.preview_area.setWidgetResizable(True)
                self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                self._single_video_player = VideoPlayerWidget(path, self._preview_container)
                self._preview_layout.addWidget(self._single_video_player)
                self._single_label.setVisible(False)
                # Autoplay on show
                try:
                    self._single_video_player.play()
                except Exception:
                    pass
            except Exception as ex:
                logger.error("Failed to load video {}: {}", path, ex)
                self._single_label.setVisible(True)
                self._single_label.setText("Video file not found or cannot be played")
        else:
            # Show image preview
            self._single_label.setVisible(True)
            self._single_label.setText("Loading…")
            self._current_single_token = self._runner.request_single_preview(path)

    def show_grid(self, items: list[tuple[str, str, str, str]]) -> None:
        self.clear()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Normalize paths and pre-sort tiles by aspect category and size to pack better
        normalized = [(normalize_windows_path(p), n, f, s) for (p, n, f, s) in items]

        def _aspect_bucket(path: str) -> int:
            # 0: landscape, 1: square/unknown, 2: portrait
            try:
                from PySide6.QtGui import QImageReader

                r = QImageReader(path)
                sz = r.size()
                w, h = sz.width(), sz.height()
                if w > 0 and h > 0:
                    if w > h:
                        return 0
                    if w == h:
                        return 1
                    return 2
            except Exception:
                pass
            return 1

        def _size_key(path: str) -> int:
            try:
                import os

                return int(os.path.getsize(path))
            except Exception:
                return 0

        # Videos first by aspect (landscape -> square -> portrait), then larger files first; keep images after videos
        videos = [it for it in normalized if is_video(it[0])]
        images = [it for it in normalized if not is_video(it[0])]
        videos.sort(key=lambda it: (_aspect_bucket(it[0]), -_size_key(it[0])))
        self._grid_items = videos + images

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(GRID_SPACING_PX)
        self._apply_grid_margins()
        cols, thumb_side = self._compute_grid_geometry()
        self._grid_labels = {}
        self._grid_video_players = {}
        self._grid_pending_video_labels = {}
        self._grid_all_players_ready = False

        # Check if any items are videos
        has_videos = any(is_video(p) for p, _, _, _ in self._grid_items)

        for i, it in enumerate(self._grid_items):
            p, name, folder, size_txt = it
            r, c = divmod(i, cols)
            tile = QWidget()
            v = QVBoxLayout(tile)
            v.setContentsMargins(0, 0, 0, 0)

            if is_video(p):
                # Video tile: thumbnail + click to play
                img_lbl = QLabel("Loading…")
                img_lbl.setFixedSize(thumb_side, thumb_side)
                img_lbl.setAlignment(Qt.AlignCenter)
                img_lbl.setStyleSheet("background-color: black;")

                # Bind with default args to capture current locals
                def _make_click_handler(
                    _path=p,
                    _tile=tile,
                    _v=v,
                    _img=img_lbl,
                    _name=name,
                    _folder=folder,
                    _size=size_txt,
                ):
                    return lambda e: self._on_video_tile_clicked(
                        _path, _tile, _v, _img, _name, _folder, _size
                    )

                img_lbl.mousePressEvent = _make_click_handler()
                v.addWidget(img_lbl)
                self._grid_pending_video_labels[p] = img_lbl

                # Add duration to info if available
                info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes\n(Duration: --:--)")
                info.setWordWrap(True)
                v.addWidget(info)
                info.setObjectName("info_label")  # Give it a unique object name

                self._grid_layout.addWidget(tile, r, c)
                token = self._runner.request_grid_thumbnail(p, thumb_side)
                self._grid_labels[token] = img_lbl
            else:
                # Image tile
                img_lbl = QLabel("Loading…")
                img_lbl.setFixedSize(thumb_side, thumb_side)
                img_lbl.setAlignment(Qt.AlignCenter)
                v.addWidget(img_lbl)
                info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes")
                info.setWordWrap(True)
                info.setObjectName("info_label")  # Give it a unique object name
                v.addWidget(info)
                self._grid_layout.addWidget(tile, r, c)
                token = self._runner.request_grid_thumbnail(p, thumb_side)
                self._grid_labels[token] = img_lbl

        self._preview_layout.addWidget(self._grid_container)

        # Add group controller if videos are present
        if has_videos:
            self._grid_media_controller = GroupMediaController(self._preview_container)
            self._preview_layout.addWidget(self._grid_media_controller)

    def clear(self) -> None:
        # Clean up single video player
        if self._single_video_player is not None:
            self._preview_layout.removeWidget(self._single_video_player)
            self._single_video_player.cleanup()
            self._single_video_player.deleteLater()
            self._single_video_player = None

        # Clean up grid video players
        for player in list(self._grid_video_players.values()):
            try:
                if self._grid_media_controller:
                    self._grid_media_controller.unregister_player(player)
            except Exception:
                pass
            try:
                player.cleanup()
            except Exception:
                pass
            try:
                player.deleteLater()
            except Exception:
                pass
        self._grid_video_players.clear()

        # Clean up group controller
        if self._grid_media_controller is not None:
            try:
                self._preview_layout.removeWidget(self._grid_media_controller)
                self._grid_media_controller.cleanup()
                self._grid_media_controller.deleteLater()
            except Exception:
                pass
            self._grid_media_controller = None

        # Clean up grid container
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
                if not lbl:
                    return

                # Update thumbnail
                if image is None:
                    lbl.setText("(failed)")
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

                # Remove misleading duration auto-update here; duration is updated by player when playing

        except Exception as ex:  # pragma: no cover - UI best effort
            logger.error("Update preview failed: {}", ex)

    def _on_video_tile_clicked(
        self,
        path: str,
        tile: QWidget,
        layout: QVBoxLayout,
        thumbnail_label: QLabel,
        name: str,
        folder: str,
        size_txt: str,
    ) -> None:
        """Handle video tile click to start playback."""
        if path in self._grid_video_players:
            # Already playing, do nothing or toggle
            return

        # Replace thumbnail with video player
        layout.removeWidget(thumbnail_label)
        thumbnail_label.hide()

        # Create video player
        try:
            video_player = VideoPlayerWidget(path, tile)
            layout.addWidget(video_player)

            # Update info label to show controls
            info_label = tile.findChild(QLabel, "info_label")
            if info_label:
                info_label.setText(f"{name}\n{folder}\n{size_txt} Bytes\n(Click to pause)")

            self._grid_video_players[path] = video_player

            # Register with group controller
            if self._grid_media_controller:
                self._grid_media_controller.register_player(video_player)

            # Store thumbnail for potential restoration
            video_player._thumbnail_pixmap = thumbnail_label.pixmap()

            # Autoplay on click
            try:
                video_player.play()
            except Exception:
                pass
        except Exception as ex:
            logger.error("Failed to load video {}: {}", path, ex)
            # Restore thumbnail on error
            layout.addWidget(thumbnail_label)
            thumbnail_label.show()
            info_label = tile.findChild(QLabel, "info_label")
            if info_label:
                info_label.setText(f"{name}\n{folder}\n{size_txt} Bytes\n(Video not available)")

        # If all videos in view have been instantiated, start group autoplay
        self._try_group_autoplay()

    def autoplay_all_videos_when_ready(self) -> None:
        """Public API: instantiate players for all visible video tiles, then autoplay."""
        try:
            if not self._grid_items or self._grid_layout is None:
                return
            # Trigger click handler for each pending video label to create players
            for p, _, _, _ in self._grid_items:
                if is_video(p) and p in self._grid_pending_video_labels:
                    lbl = self._grid_pending_video_labels.get(p)
                    if lbl and hasattr(lbl, "mousePressEvent"):
                        try:
                            lbl.mousePressEvent(None)  # synthesize click
                        except Exception:
                            pass
        finally:
            self._try_group_autoplay()

    def _try_group_autoplay(self) -> None:
        """If all visible videos have players, autoplay all via controller."""
        if not self._grid_items:
            return
        pending = [
            p
            for (p, _, _, _) in self._grid_items
            if is_video(p) and p not in self._grid_video_players
        ]
        if pending:
            return
        if self._grid_all_players_ready:
            return
        self._grid_all_players_ready = True
        # Register all players with controller
        if self._grid_media_controller:
            for player in self._grid_video_players.values():
                try:
                    self._grid_media_controller.register_player(player)
                except Exception:
                    pass
            # Start playback
            try:
                self._grid_media_controller.playRequested.emit()
            except Exception:
                pass

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

            # Check if any items are videos for controller
            has_videos = any(is_video(p) for p, _, _, _ in self._grid_items)

            for i, it in enumerate(self._grid_items):
                p, name, folder, size_txt = it
                r, c = divmod(i, cols)
                tile = QWidget()
                v = QVBoxLayout(tile)
                v.setContentsMargins(0, 0, 0, 0)

                if is_video(p):
                    # Video tile
                    img_lbl = QLabel("Loading…")
                    img_lbl.setFixedSize(thumb_side, thumb_side)
                    img_lbl.setAlignment(Qt.AlignCenter)
                    img_lbl.setStyleSheet("background-color: black;")

                    def _make_click_handler2(
                        _path=p,
                        _tile=tile,
                        _v=v,
                        _img=img_lbl,
                        _name=name,
                        _folder=folder,
                        _size=size_txt,
                    ):
                        return lambda e: self._on_video_tile_clicked(
                            _path, _tile, _v, _img, _name, _folder, _size
                        )

                    img_lbl.mousePressEvent = _make_click_handler2()
                    v.addWidget(img_lbl)

                    info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes\n(Duration: --:--)")
                    info.setWordWrap(True)
                    info.setObjectName("info_label")
                    v.addWidget(info)

                    self._grid_layout.addWidget(tile, r, c)
                    token = self._runner.request_grid_thumbnail(p, thumb_side)
                    self._grid_labels[token] = img_lbl
                else:
                    # Image tile
                    img_lbl = QLabel("Loading…")
                    img_lbl.setFixedSize(thumb_side, thumb_side)
                    img_lbl.setAlignment(Qt.AlignCenter)
                    v.addWidget(img_lbl)
                    info = QLabel(f"{name}\n{folder}\n{size_txt} Bytes")
                    info.setWordWrap(True)
                    info.setObjectName("info_label")
                    v.addWidget(info)
                    self._grid_layout.addWidget(tile, r, c)
                    token = self._runner.request_grid_thumbnail(p, thumb_side)
                    self._grid_labels[token] = img_lbl

            self._preview_layout.addWidget(self._grid_container)

            # Add group controller if videos are present
            if has_videos:
                if self._grid_media_controller is None:
                    self._grid_media_controller = GroupMediaController(self._preview_container)
                    self._preview_layout.addWidget(self._grid_media_controller)
                    # Re-register existing players
                    for player in self._grid_video_players.values():
                        self._grid_media_controller.register_player(player)
                # After rebuild, attempt autoplay if all are ready
                self._try_group_autoplay()
            elif self._grid_media_controller is not None:
                # Remove controller if no videos
                self._preview_layout.removeWidget(self._grid_media_controller)
                self._grid_media_controller.cleanup()
                self._grid_media_controller.deleteLater()
                self._grid_media_controller = None
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
        # Reduce margins to utilize space better in grid mode
        m_lr = int(w * (GRID_MARGIN_RATIO * 0.5))
        m_tb = int(h * (GRID_MARGIN_RATIO * 0.5))
        self._grid_layout.setContentsMargins(m_lr, m_tb, m_lr, m_tb)

    def _compute_grid_geometry(self) -> tuple[int, int]:
        viewport = self.preview_area.viewport()
        width = max(1, viewport.width())
        spacing = max(2, GRID_SPACING_PX // 2)
        min_px = max(150, GRID_MIN_THUMB_PX - 50)
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
