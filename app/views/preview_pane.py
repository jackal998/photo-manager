from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
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
from app.views.preview_pane_helpers import (
    aspect_bucket_from_resolution,
    attach_resolutions,
    build_info_rows,
    classify_image_token,
    compute_fit_width,
    compute_grid_geometry,
    format_info_html,
    format_resolution_string,
    get_file_size_bytes,
    normalize_grid_items,
)
from app.views.widgets.group_media_controller import GroupMediaController
from app.views.widgets.video_player import VideoPlayerWidget
from infrastructure.i18n import t

_RAW_EXTENSIONS = frozenset((".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2"))


def _raw_sensor_dims(path: str) -> tuple[int, int]:
    """Return (width, height) from rawpy metadata for RAW/DNG files, or (0, 0).

    rawpy reads the sensor size from the RAW metadata, not from an embedded
    JPEG thumbnail, so this always reflects the true capture resolution.
    """
    try:
        import rawpy  # type: ignore
        with rawpy.imread(path) as raw:
            w, h = raw.sizes.width, raw.sizes.height
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return 0, 0


def _read_resolution(path: str) -> str | None:
    """Return "W×H" pixel dimensions for an image file, or None on failure.

    For RAW/DNG files rawpy is tried first — QImageReader has no DNG decoder
    and PIL's TIFF reader returns the embedded thumbnail dimensions instead of
    the true sensor size.  For other formats: QImageReader (header-only I/O),
    then PIL as fallback for HEIC and other Qt-unsupported formats.
    """
    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    if f".{ext}" in _RAW_EXTENSIONS:
        w, h = _raw_sensor_dims(path)
        if w > 0 and h > 0:
            return f"{w}×{h}"
    try:
        r = QImageReader(path)
        sz = r.size()
        w, h = sz.width(), sz.height()
        if w > 0 and h > 0:
            return f"{w}×{h}"
    except Exception:
        pass
    try:
        from PIL import Image
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as img:
            w, h = img.size
            if w > 0 and h > 0:
                return f"{w}×{h}"
    except Exception:
        pass
    return None


class PreviewPane(QWidget):
    """Encapsulates the right-side preview (single image / grid)."""

    # Emitted when the user double-clicks a preview tile or the single-view
    # image; MainWindow connects this to on_open_full_res_viewer(path).
    requestFullRes = Signal(str)

    def __init__(
        self, parent: QWidget | None, task_runner: ImageTaskRunner, thumb_size: int | None = None
    ) -> None:
        super().__init__(parent)
        self._runner = task_runner
        self._thumb_size = int(thumb_size or DEFAULT_THUMB_SIZE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(QLabel(t("preview.header")))

        self.preview_area = QScrollArea()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.preview_area.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self._preview_container = QWidget()
        self._preview_layout = QVBoxLayout(self._preview_container)
        self._preview_layout.setContentsMargins(0, 0, 0, 0)

        # Persistent file info header (shown for both image and video single-view)
        self._single_info_label = QLabel()
        self._single_info_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._single_info_label.setWordWrap(True)
        self._single_info_label.setVisible(False)
        self._preview_layout.addWidget(self._single_info_label)

        self._single_label = QLabel(t("preview.placeholder"))
        self._single_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._single_label.setMinimumHeight(200)
        self._preview_layout.addWidget(self._single_label)
        # Wire double-click → requestFullRes signal; path captured per show_single call.
        self._single_label_path: str | None = None
        self._single_label.mouseDoubleClickEvent = self._on_single_label_double_click

        self.preview_area.setWidget(self._preview_container)
        root.addWidget(self.preview_area)

        # state
        self._current_single_token: str | None = None
        self._grid_labels: dict[str, QLabel] = {}
        self._grid_container: QWidget | None = None
        self._grid_layout: QGridLayout | None = None
        self._grid_items: list[tuple[str, str, str, str, str, str, str]] = []
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
    def show_single(self, path: str, info: dict | None = None) -> None:
        self.clear()
        self._single_label_path = path
        self.preview_area.setWidgetResizable(False)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        if is_video(path):
            # Show video player with info header above
            try:
                self.preview_area.setWidgetResizable(True)
                self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                # Reset vertical to AsNeeded — only single-image view reserves
                # it (the player manages its own size, no fit-on-width loop).
                self.preview_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                if info and isinstance(info, dict):
                    info_rows = build_info_rows(
                        name=info.get("name") or "",
                        folder=info.get("folder") or "",
                        size_txt=info.get("size") or "",
                        creation_txt=info.get("creation") or "",
                        shot_txt=info.get("shot") or "",
                    )
                    html = format_info_html(info_rows)
                    if html:
                        self._single_info_label.setTextFormat(Qt.RichText)
                        self._single_info_label.setText(html)
                        self._single_info_label.setVisible(True)
                self._single_video_player = VideoPlayerWidget(path, self._preview_container)
                self._preview_layout.addWidget(self._single_video_player)
                self._single_label.setVisible(False)
                # Autoplay is intentionally disabled — user must click Play.
            except Exception as ex:
                logger.error("Failed to load video {}: {}", path, ex)
                self._single_info_label.clear()
                self._single_info_label.setVisible(False)
                self._single_label.setVisible(True)
                self._single_label.setText(t("preview.video_unavailable"))
        else:
            # Show image preview with persistent info block.
            #
            # Reserve the vertical scrollbar (AlwaysOn) for single-image view.
            # The fit-on-width path recomputes the pixmap to the *viewport*
            # width on every Resize (see ``eventFilter`` → ``_apply_single_pixmap_fit``).
            # With ``AsNeeded`` a tall (portrait) image whose fitted height
            # straddles the viewport makes the scrollbar appear → viewport
            # narrows → refit → image now fits → scrollbar disappears →
            # viewport widens → refit → … an unbounded resize⇄refit loop that
            # pegs the UI thread at 100% CPU and never settles (the
            # Close-&-Load freeze: a portrait keeper auto-previewed at the
            # boundary width). Reserving the scrollbar keeps the viewport width
            # constant, so the fit converges in one pass. Reset to AsNeeded in
            # the video / grid branches below.
            self.preview_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            self._single_label.setVisible(True)
            if info and isinstance(info, dict):
                res = _read_resolution(normalize_windows_path(path))
                info_rows = build_info_rows(
                    name=info.get("name") or "",
                    folder=info.get("folder") or "",
                    size_txt=info.get("size") or "",
                    creation_txt=info.get("creation") or "",
                    shot_txt=info.get("shot") or "",
                    resolution=res or "",
                )
                html = format_info_html(info_rows)
                if html:
                    self._single_info_label.setTextFormat(Qt.RichText)
                    self._single_info_label.setText(html)
                    self._single_info_label.setVisible(True)
            self._single_label.setText(t("preview.loading"))
            self._current_single_token = self._runner.request_single_preview(path)

    def show_grid(
        self, items: list[tuple[str, str, str, str] | tuple[str, str, str, str, str, str]]
    ) -> None:
        self.clear()
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Reset vertical to AsNeeded — only single-image view reserves it.
        self.preview_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Normalize paths; read image dimensions once per image (header-only) for both
        # aspect-ratio sort and resolution display — avoids double QImageReader opens.
        # Tuple layout: (path, name, folder, size_txt, creation_txt, shot_txt, resolution)
        # resolution is "W*H" for images, "" for videos.
        def _image_dims(path: str) -> tuple[int, int]:
            ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
            if f".{ext}" in _RAW_EXTENSIONS:
                w, h = _raw_sensor_dims(path)
                if w > 0 and h > 0:
                    return w, h
            try:
                sz = QImageReader(path).size()
                w, h = sz.width(), sz.height()
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                pass
            try:
                from PIL import Image
                try:
                    from pillow_heif import register_heif_opener
                    register_heif_opener()
                except ImportError:
                    pass
                with Image.open(path) as img:
                    w, h = img.size
                    if w > 0 and h > 0:
                        return w, h
            except Exception:
                pass
            return 0, 0

        normalized = normalize_grid_items(items, normalize_windows_path)
        result = attach_resolutions(normalized, _image_dims, is_video)

        # Videos first by aspect (landscape→square→portrait), then larger first; images after.
        videos = [it for it in result if is_video(it[0])]
        images = [it for it in result if not is_video(it[0])]

        videos.sort(
            key=lambda it: (aspect_bucket_from_resolution(it[6]), -get_file_size_bytes(it[0]))
        )
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
        has_videos = any(is_video(it[0]) for it in self._grid_items)

        for i, it in enumerate(self._grid_items):
            p, name, folder, size_txt, creation_txt, shot_txt, res = it
            r, c = divmod(i, cols)
            tile = QWidget()
            v = QVBoxLayout(tile)
            v.setContentsMargins(0, 0, 0, 0)

            if is_video(p):
                # Video tile: thumbnail + click to play
                img_lbl = QLabel(t("preview.loading"))
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

                tile_rows = build_info_rows(
                    name=name,
                    folder=folder,
                    size_txt=size_txt,
                    creation_txt=creation_txt,
                    shot_txt=shot_txt,
                    duration_unknown=True,
                )
                info = QLabel(format_info_html(tile_rows))
                info.setTextFormat(Qt.RichText)
                info.setWordWrap(True)
                v.addWidget(info)
                info.setObjectName("info_label")

                self._grid_layout.addWidget(tile, r, c)
                token = self._runner.request_grid_thumbnail(p, thumb_side)
                self._grid_labels[token] = img_lbl
            else:
                # Image tile
                img_lbl = QLabel(t("preview.loading"))
                img_lbl.setFixedSize(thumb_side, thumb_side)
                img_lbl.setAlignment(Qt.AlignCenter)

                # Double-click on a grid image tile → open full-res viewer
                def _make_dblclick_handler(_path=p):
                    return lambda e: self.requestFullRes.emit(_path)

                img_lbl.mouseDoubleClickEvent = _make_dblclick_handler()
                v.addWidget(img_lbl)
                tile_rows = build_info_rows(
                    name=name,
                    folder=folder,
                    size_txt=size_txt,
                    creation_txt=creation_txt,
                    shot_txt=shot_txt,
                    resolution=res,
                )
                info = QLabel(format_info_html(tile_rows))
                info.setTextFormat(Qt.RichText)
                info.setWordWrap(True)
                info.setObjectName("info_label")
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
        self._single_info_label.clear()
        self._single_info_label.setVisible(False)
        self._single_label.clear()
        self._single_label.setVisible(False)
        self._single_pm = None
        self._single_label_path = None

    def toggle_play_pause(self) -> None:
        """Toggle playback on the single-view video player, if any.

        Triggered by the P shortcut on the main tree (#615 follow-up
        for the no-autoplay default introduced in #624). Single-view
        only — in grid mode there is no unambiguous "focused" player,
        so the shortcut becomes a no-op rather than picking
        arbitrarily. Safe to call when no video is currently shown
        (single video player is None) — also a silent no-op.

        Reads the player's current ``is_playing()`` state and branches
        to ``pause()`` or ``play()``; the player itself owns the
        ``QMediaPlayer`` so this method needs no further state.
        """
        player = self._single_video_player
        if player is None:
            return
        try:
            if player.is_playing():
                player.pause()
            else:
                player.play()
        except Exception:
            # Best-effort UX; never raise from a keyboard-shortcut slot.
            pass

    def release_file_handles(self) -> None:
        """Release any open media/file handles held by the preview.

        - Stops and detaches the single video player if present
        - Stops and detaches any grid video players
        - Clears QPixmaps to drop file-backed resources
        """
        try:
            # Stop single video player
            if self._single_video_player is not None:
                try:
                    self._single_video_player.cleanup()
                except Exception:
                    pass
                try:
                    self._preview_layout.removeWidget(self._single_video_player)
                except Exception:
                    pass
                try:
                    self._single_video_player.deleteLater()
                except Exception:
                    pass
                self._single_video_player = None

            # Stop grid video players
            for player in list(self._grid_video_players.values()):
                try:
                    player.cleanup()
                except Exception:
                    pass
                try:
                    player.deleteLater()
                except Exception:
                    pass
            self._grid_video_players.clear()

            # Clear pixmaps to free resources
            try:
                self._single_label.clear()
            except Exception:
                pass

        except Exception:
            # Best-effort; never raise from a release
            pass

    def on_image_loaded(self, token: str, path: str, image: Any) -> None:
        try:
            kind = classify_image_token(token)
            if kind == "single":
                if token != self._current_single_token:
                    return
                if image is None:
                    self._single_label.setText(t("preview.failed"))
                    return
                pm = QPixmap.fromImage(image)
                if pm.isNull():
                    self._single_label.setText(t("preview.failed"))
                    return
                self._single_pm = pm
                self._apply_single_pixmap_fit()
                self._single_label.setText("")
            elif kind == "grid":
                lbl = self._grid_labels.get(token)
                if not lbl:
                    return

                # Update thumbnail
                if image is None:
                    lbl.setText(t("preview.failed"))
                    return
                pm = QPixmap.fromImage(image)
                if pm.isNull():
                    lbl.setText(t("preview.failed"))
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
                size_value = t("preview.info_size_value", bytes=size_txt)
                info_label.setText(
                    f"{name}\n{folder}\n{size_value}\n{t('preview.click_to_pause')}"
                )

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
                size_value = t("preview.info_size_value", bytes=size_txt)
                info_label.setText(
                    f"{name}\n{folder}\n{size_value}\n{t('preview.video_not_available')}"
                )

        # If all videos in view have been instantiated, start group autoplay
        self._try_group_autoplay()

    def autoplay_all_videos_when_ready(self) -> None:
        """Public API: no-op — autoplay is disabled (#622 Phase 1).

        Videos require explicit user interaction (click Play) to start.
        Kept for API compatibility; callers that previously relied on this
        to bootstrap grid playback will see no effect.
        """

    def _try_group_autoplay(self) -> None:
        """If all visible videos have players, autoplay all via controller."""
        if not self._grid_items:
            return
        pending = [
            it[0]
            for it in self._grid_items
            if is_video(it[0]) and it[0] not in self._grid_video_players
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

    def _on_single_label_double_click(self, event: Any) -> None:
        """Double-click on the single-view label → emit requestFullRes."""
        if self._single_label_path:
            self.requestFullRes.emit(self._single_label_path)

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
            has_videos = any(is_video(it[0]) for it in self._grid_items)

            for i, it in enumerate(self._grid_items):
                p, name, folder, size_txt, creation_txt, shot_txt, res = it
                r, c = divmod(i, cols)
                tile = QWidget()
                v = QVBoxLayout(tile)
                v.setContentsMargins(0, 0, 0, 0)

                if is_video(p):
                    # Video tile
                    img_lbl = QLabel(t("preview.loading"))
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

                    extra = []
                    if creation_txt:
                        extra.append(f"{t('preview.info_created')}: {creation_txt}")
                    if shot_txt:
                        extra.append(f"{t('preview.info_shot')}: {shot_txt}")
                    extra_txt = ("\n" + "\n".join(extra)) if extra else ""
                    size_value = t("preview.info_size_value", bytes=size_txt)
                    duration_unknown = t("preview.info_duration_unknown")
                    duration_label = t("preview.info_duration")
                    info = QLabel(
                        f"{name}\n{folder}\n{size_value}{extra_txt}\n"
                        f"({duration_label}: {duration_unknown})"
                    )
                    info.setWordWrap(True)
                    info.setObjectName("info_label")
                    v.addWidget(info)

                    self._grid_layout.addWidget(tile, r, c)
                    token = self._runner.request_grid_thumbnail(p, thumb_side)
                    self._grid_labels[token] = img_lbl
                else:
                    # Image tile — resolution comes from cached _grid_items, no extra I/O
                    img_lbl = QLabel(t("preview.loading"))
                    img_lbl.setFixedSize(thumb_side, thumb_side)
                    img_lbl.setAlignment(Qt.AlignCenter)
                    v.addWidget(img_lbl)
                    extra = []
                    if creation_txt:
                        extra.append(f"{t('preview.info_created')}: {creation_txt}")
                    if shot_txt:
                        extra.append(f"{t('preview.info_shot')}: {shot_txt}")
                    extra_txt = ("\n" + "\n".join(extra)) if extra else ""
                    res_txt = f"\n{res}" if res else ""
                    size_value = t("preview.info_size_value", bytes=size_txt)
                    info = QLabel(f"{name}\n{folder}\n{size_value}{res_txt}{extra_txt}")
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
        spacing = max(2, GRID_SPACING_PX // 2)
        min_px = max(150, GRID_MIN_THUMB_PX - 50)
        try:
            max_px = int(self._thumb_size)
        except Exception:
            max_px = 0
        return compute_grid_geometry(
            viewport_width=viewport.width(),
            thumb_size_max=max_px,
            spacing=spacing,
            min_px=min_px,
        )

    def _apply_single_pixmap_fit(self) -> None:
        try:
            if self._grid_container is not None and self._grid_items:
                return
            if self._single_pm is None or self._single_pm.isNull():
                return
            vp = self.preview_area.viewport()
            pm = self._single_pm
            target_w = compute_fit_width(pm.width(), vp.width())
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
