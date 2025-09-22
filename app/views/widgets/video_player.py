"""Video player widget with basic controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.views.media_utils import format_duration


class VideoPlayerWidget(QWidget):
    """Video player widget with play/pause, volume, and progress controls.

    Uses Qt's default media player behavior without custom UI overrides.
    """

    # Signals for group controller integration
    durationChanged = Signal(int)  # duration in milliseconds
    positionChanged = Signal(int)  # position in milliseconds
    stateChanged = Signal(QMediaPlayer.PlaybackState)

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._path = path
        self._duration = 0
        self._last_position = 0
        self._slider_dragging = False

        # Setup media player
        self._media_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._video_widget = QVideoWidget(self)
        self._media_player.setVideoOutput(self._video_widget)

        # Connect signals
        self._media_player.durationChanged.connect(self._on_duration_changed)
        self._media_player.positionChanged.connect(self._on_position_changed)
        self._media_player.playbackStateChanged.connect(self._on_state_changed)

        # Load video first
        try:
            # Ensure local file path is correctly mapped
            if not path.lower().startswith(("file://",)):
                source = QUrl.fromLocalFile(path)
            else:
                source = QUrl(path)
            self._media_player.setSource(source)
        except Exception as ex:
            logger.error("Failed to load video {}: {}", path, ex)
            self._video_load_error = True
        else:
            self._video_load_error = False

        # Setup UI
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the video player UI with controls."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Video widget (fills available space). Use expanding policies and keep aspect.
        self._video_widget.setMinimumSize(200, 150)
        self._video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._video_widget)

        # Show error message if video failed to load
        if hasattr(self, "_video_load_error") and self._video_load_error:
            self._video_widget.hide()
            error_label = QLabel("Video file not found or cannot be played")
            error_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(error_label)

        # Controls layout
        controls = QHBoxLayout()

        # Play/Pause button
        self._play_button = QPushButton("▶")
        self._play_button.setFixedSize(30, 30)
        self._play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self._play_button)

        # Progress slider
        self._progress_slider = QSlider(Qt.Horizontal)
        self._progress_slider.setRange(0, 0)  # Will be set when duration is known
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)
        self._progress_slider.valueChanged.connect(self._on_slider_value_changed)
        controls.addWidget(self._progress_slider)

        # Time labels
        self._current_time = QPushButton("--:--")
        self._current_time.setFlat(True)
        self._current_time.setStyleSheet("border: none; background: transparent;")
        self._current_time.clicked.connect(lambda: self._seek_to(0))
        controls.addWidget(self._current_time)

        controls.addStretch()

        # Duration label
        self._duration_label = QPushButton("--:--")
        self._duration_label.setFlat(True)
        self._duration_label.setStyleSheet("border: none; background: transparent;")
        controls.addWidget(self._duration_label)

        # Volume slider
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(50)
        self._volume_slider.setFixedWidth(100)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        controls.addWidget(self._volume_slider)

        # Volume icon
        self._volume_button = QPushButton("🔊")
        self._volume_button.setFixedSize(30, 30)
        self._volume_button.clicked.connect(self._toggle_mute)
        controls.addWidget(self._volume_button)

        layout.addLayout(controls)

        # Update UI based on state
        self._update_play_button()

        # If duration already known (signal arrived early), apply it now
        if getattr(self, "_duration", 0) > 0:
            try:
                self._progress_slider.setRange(0, self._duration)
                self._duration_label.setText(format_duration(self._duration))
            except Exception:
                pass

    def _toggle_playback(self) -> None:
        """Toggle play/pause state."""
        if self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._media_player.pause()
        else:
            self._media_player.play()

    def _toggle_mute(self) -> None:
        """Toggle mute state."""
        self._audio_output.setMuted(not self._audio_output.isMuted())
        self._update_volume_button()

    def _on_volume_changed(self, value: int) -> None:
        """Handle volume slider changes."""
        self._audio_output.setVolume(value / 100.0)

    def _on_slider_pressed(self) -> None:
        """Handle slider press - pause updates."""
        self._slider_dragging = True

    def _on_slider_released(self) -> None:
        """Handle slider release - resume updates and seek."""
        self._slider_dragging = False
        position = self._progress_slider.value()
        self._media_player.setPosition(position)

    def _on_slider_value_changed(self, value: int) -> None:
        """Handle slider value changes during drag."""
        if self._slider_dragging:
            # Update time display during drag
            self._update_current_time(value)

    def _seek_to(self, position: int) -> None:
        """Seek to specific position."""
        self._media_player.setPosition(position)

    def _update_play_button(self) -> None:
        """Update play button text based on state."""
        if self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._play_button.setText("⏸")
        else:
            self._play_button.setText("▶")

    def _update_volume_button(self) -> None:
        """Update volume button icon based on mute state."""
        if self._audio_output.isMuted():
            self._volume_button.setText("🔇")
        else:
            self._volume_button.setText("🔊")

    def _on_duration_changed(self, duration: int) -> None:
        """Handle duration change."""
        self._duration = duration
        # Guard: slider may not be constructed yet if signal arrives very early
        if hasattr(self, "_progress_slider"):
            self._progress_slider.setRange(0, duration)
        if hasattr(self, "_duration_label"):
            self._duration_label.setText(format_duration(duration))
        self.durationChanged.emit(duration)

    def _on_position_changed(self, position: int) -> None:
        """Handle position change."""
        if not self._slider_dragging:
            self._progress_slider.setValue(position)
            self._update_current_time(position)
        self._last_position = position
        self.positionChanged.emit(position)

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Handle playback state change."""
        self._update_play_button()
        self.stateChanged.emit(state)

    def _update_current_time(self, position: int) -> None:
        """Update current time display."""
        self._current_time.setText(format_duration(position))

    # Public API
    def play(self) -> None:
        """Start playback."""
        # Ensure play even if media isn't ready yet
        try:
            self._media_player.play()
        except RuntimeError:
            pass

    def pause(self) -> None:
        """Pause playback."""
        self._media_player.pause()

    def stop(self) -> None:
        """Stop playback."""
        self._media_player.stop()

    def set_position(self, position: int) -> None:
        """Set playback position in milliseconds."""
        self._media_player.setPosition(position)

    def set_volume(self, volume: float) -> None:
        """Set volume (0.0 to 1.0)."""
        self._audio_output.setVolume(volume)
        self._volume_slider.setValue(int(volume * 100))

    def get_position(self) -> int:
        """Get current position in milliseconds."""
        return self._media_player.position()

    def get_duration(self) -> int:
        """Get duration in milliseconds."""
        return self._duration

    def is_playing(self) -> bool:
        """Check if currently playing."""
        return self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def get_last_position(self) -> int:
        """Get the last known position."""
        return self._last_position

    def set_last_position(self, position: int) -> None:
        """Set the last known position."""
        self._last_position = position

    def cleanup(self) -> None:
        """Clean up resources."""
        try:
            if getattr(self, "_media_player", None):
                try:
                    self._media_player.stop()
                except RuntimeError:
                    pass
                try:
                    self._media_player.setSource(QUrl())
                except RuntimeError:
                    pass
                try:
                    self._media_player.deleteLater()
                except RuntimeError:
                    pass
            if getattr(self, "_audio_output", None):
                try:
                    self._audio_output.deleteLater()
                except RuntimeError:
                    pass
        except Exception:
            pass
