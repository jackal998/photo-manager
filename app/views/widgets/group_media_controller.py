"""Group media controller for synchronized video playback."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.views.media_utils import format_duration
from app.views.widgets.video_player import VideoPlayerWidget


class GroupMediaController(QWidget):
    """Controller for synchronized playback of multiple video players.

    Broadcasts play/pause, volume, and position changes to all registered players.
    """

    # Signals for broadcasting to players
    playRequested = Signal()
    pauseRequested = Signal()
    positionRequested = Signal(int)  # position in milliseconds
    volumeRequested = Signal(float)  # volume 0.0-1.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._players: list[VideoPlayerWidget] = []
        self._master_duration = 0
        self._master_position = 0
        self._is_playing = False
        self._slider_dragging = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the group controller UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Play/Pause button
        self._play_button = QPushButton("▶")
        self._play_button.setFixedSize(40, 30)
        self._play_button.clicked.connect(self._toggle_playback)

        # Progress slider
        self._progress_slider = QSlider(Qt.Horizontal)
        self._progress_slider.setRange(0, 0)
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)
        self._progress_slider.valueChanged.connect(self._on_slider_value_changed)

        # Time labels
        self._current_time = QPushButton("--:--")
        self._current_time.setFlat(True)
        self._current_time.setStyleSheet("border: none; background: transparent;")

        self._duration_label = QPushButton("--:--")
        self._duration_label.setFlat(True)
        self._duration_label.setStyleSheet("border: none; background: transparent;")

        # Volume slider
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(50)
        self._volume_slider.setFixedWidth(120)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        # Volume icon
        self._volume_button = QPushButton("🔊")
        self._volume_button.setFixedSize(30, 30)
        self._volume_button.clicked.connect(self._toggle_mute)

        # Layout controls
        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self._play_button)
        controls_layout.addWidget(self._progress_slider)
        controls_layout.addWidget(self._current_time)
        controls_layout.addStretch()
        controls_layout.addWidget(self._duration_label)
        controls_layout.addWidget(self._volume_slider)
        controls_layout.addWidget(self._volume_button)

        layout.addLayout(controls_layout)

        # Update UI elements
        self._update_play_button()
        self._update_volume_button()

    def register_player(self, player: VideoPlayerWidget) -> None:
        """Register a video player for synchronized control.

        Args:
            player: VideoPlayerWidget to register
        """
        if player not in self._players:
            self._players.append(player)

            # Connect to player's signals for UI updates
            player.durationChanged.connect(self._on_player_duration_changed)
            player.positionChanged.connect(self._on_player_position_changed)
            player.stateChanged.connect(self._on_player_state_changed)

            # Connect controller signals to player
            self.playRequested.connect(player.play)
            self.pauseRequested.connect(player.pause)
            self.positionRequested.connect(player.set_position)
            self.volumeRequested.connect(player.set_volume)

    def unregister_player(self, player: VideoPlayerWidget) -> None:
        """Unregister a video player.

        Args:
            player: VideoPlayerWidget to unregister
        """
        if player in self._players:
            self._players.remove(player)

            # Disconnect signals
            for fn in (
                lambda: player.durationChanged.disconnect(self._on_player_duration_changed),
                lambda: player.positionChanged.disconnect(self._on_player_position_changed),
                lambda: player.stateChanged.disconnect(self._on_player_state_changed),
                lambda: self.playRequested.disconnect(player.play),
                lambda: self.pauseRequested.disconnect(player.pause),
                lambda: self.positionRequested.disconnect(player.set_position),
                lambda: self.volumeRequested.disconnect(player.set_volume),
            ):
                try:
                    fn()
                except (TypeError, RuntimeError):
                    pass

    def _toggle_playback(self) -> None:
        """Toggle play/pause for all registered players."""
        if self._is_playing:
            self.pauseRequested.emit()
        else:
            self.playRequested.emit()

    def _toggle_mute(self) -> None:
        """Toggle mute for all registered players."""
        # Mute state is per-player, so we broadcast volume 0 or current volume
        if self._volume_slider.value() > 0:
            self._volume_slider.setValue(0)
        else:
            self._volume_slider.setValue(50)

    def _on_volume_changed(self, value: int) -> None:
        """Handle volume slider changes."""
        volume = value / 100.0
        self._update_volume_button()
        self.volumeRequested.emit(volume)

    def _on_slider_pressed(self) -> None:
        """Handle slider press."""
        self._slider_dragging = True

    def _on_slider_released(self) -> None:
        """Handle slider release."""
        self._slider_dragging = False
        position = self._progress_slider.value()
        self.positionRequested.emit(position)

    def _on_slider_value_changed(self, value: int) -> None:
        """Handle slider value changes during drag."""
        if self._slider_dragging:
            self._update_current_time(value)

    def _on_player_duration_changed(self, duration: int) -> None:
        """Handle duration change from a player."""
        if duration > self._master_duration:
            self._master_duration = duration
            self._progress_slider.setRange(0, duration)
            self._duration_label.setText(format_duration(duration))

    def _on_player_position_changed(self, position: int) -> None:
        """Handle position change from a player."""
        if not self._slider_dragging:
            # Update master position from the first playing player
            if not self._is_playing:
                self._master_position = position
                self._progress_slider.setValue(position)
                self._update_current_time(position)

    def _on_player_state_changed(self, state: Any) -> None:
        """Handle state change from a player."""
        # Update master playing state based on majority
        playing_count = sum(1 for p in self._players if p.is_playing())
        self._is_playing = playing_count > len(self._players) // 2
        self._update_play_button()

    def _update_play_button(self) -> None:
        """Update play button text."""
        if self._is_playing:
            self._play_button.setText("⏸")
        else:
            self._play_button.setText("▶")

    def _update_volume_button(self) -> None:
        """Update volume button icon."""
        if self._volume_slider.value() == 0:
            self._volume_button.setText("🔇")
        else:
            self._volume_button.setText("🔊")

    def _update_current_time(self, position: int) -> None:
        """Update current time display."""
        self._current_time.setText(format_duration(position))

    # Public API
    def cleanup(self) -> None:
        """Clean up resources and unregister all players."""
        for player in list(self._players):
            self.unregister_player(player)
        self._players.clear()

    def get_registered_count(self) -> int:
        """Get number of registered players."""
        return len(self._players)

    def set_position_sync(self, position_ratio: float) -> None:
        """Set position based on ratio (0.0-1.0) for all players.

        Args:
            position_ratio: Position ratio from 0.0 to 1.0
        """
        if self._master_duration > 0:
            position = int(position_ratio * self._master_duration)
            self.positionRequested.emit(position)
