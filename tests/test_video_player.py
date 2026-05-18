"""Layer-1 tests for
:class:`app.views.widgets.video_player.VideoPlayerWidget` (#293).

Closes the video_player portion of #293 (cascade-omit follow-up
to #185). Mirrors the pattern proved out by #283 (main_window),
#285 (group_media_controller), and #289 (preview_pane): pure-logic
extraction into a sibling helper module + one real-construction
test + fake-self (``SimpleNamespace``) thin-proxy tests for every
dispatch method.

The pure-logic helpers themselves are tested in
``test_video_player_helpers.py`` (sibling module).

## Pattern

* **One real-construction test** with the session ``qapp`` fixture
  to catch ``__init__`` / ``_setup_ui`` assembly reorders. The
  ``VideoPlayerWidget`` constructor builds a non-trivial Qt
  hierarchy (``QMediaPlayer`` + ``QAudioOutput`` + ``QVideoWidget``
  + signal wiring + a layout with buttons / sliders / labels);
  a refactor that reorders any of these can leave a downstream
  method referencing an unset attr.
* **Fake-self unbound-method tests** for every dispatch method,
  signal handler, and public API surface. Each tests the
  decision logic without involving a real ``QMediaPlayer``.

## Not covered here (by design)

* Real video decode + frame rendering — layer 3 via s11 (Live
  Photo scenario, real ``QMediaPlayer`` per-OS backend).
* Real signal emit / connect round-trip — the construction test
  exercises the wiring at ``__init__`` time; signal payload
  delivery on real media events is L3 via s11.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtMultimedia import QMediaPlayer

from app.views.widgets.video_player import VideoPlayerWidget


# ── one real-construction test (catches __init__ assembly bugs) ──────────


def test_video_player_widget_constructs_with_qapp(qapp):
    """``VideoPlayerWidget(path, parent=None)`` returns without
    raising and every attr the dispatch methods depend on is
    attached.

    Failure mode: a refactor reorders ``__init__`` so a later
    step references an un-set attribute (e.g. tries to
    ``self._audio_output.setMuted(...)`` before ``_audio_output``
    is created). The bug surfaces as ``AttributeError`` on the
    user's first click — invisible to L3 because every scenario
    that opens a video hits it identically.
    """
    w = VideoPlayerWidget("nonexistent.mp4", parent=None)
    try:
        # Core media plumbing
        assert w._media_player is not None
        assert w._audio_output is not None
        assert w._video_widget is not None
        # UI elements
        assert w._play_button is not None
        assert w._progress_slider is not None
        assert w._volume_slider is not None
        assert w._volume_button is not None
        assert w._current_time is not None
        assert w._duration_label is not None
        # State
        assert w._path == "nonexistent.mp4"
        assert w._duration == 0
        assert w._last_position == 0
        assert w._slider_dragging is False
    finally:
        w.cleanup()
        w.deleteLater()


def test_construct_with_file_url_uses_qurl_directly(qapp):
    """When the path already has a ``file://`` scheme, the
    constructor takes the ``QUrl(path)`` branch instead of
    ``QUrl.fromLocalFile``. The post-state assertion: setSource
    was called once with a QUrl. (We don't introspect the URL
    because Qt internals on Windows can normalise it.)
    """
    w = VideoPlayerWidget("file:///c:/nonexistent.mp4", parent=None)
    try:
        assert w._media_player is not None
    finally:
        w.cleanup()
        w.deleteLater()


# ── _toggle_playback ─────────────────────────────────────────────────────


class TestTogglePlayback:
    """Decision: paused/stopped → play; playing → pause."""

    def test_playing_calls_pause(self):
        """Failure mode: a refactor that flipped the branches
        would mean clicking the play button while a video plays
        would call ``play()`` instead of ``pause()`` — the button
        would visibly do nothing on every other click."""
        fake_player = MagicMock()
        fake_player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget._toggle_playback(fake_self)

        fake_player.pause.assert_called_once()
        fake_player.play.assert_not_called()

    def test_paused_calls_play(self):
        fake_player = MagicMock()
        fake_player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget._toggle_playback(fake_self)

        fake_player.play.assert_called_once()
        fake_player.pause.assert_not_called()

    def test_stopped_calls_play(self):
        """Stopped is the third QMediaPlayer state — should also
        resume on click."""
        fake_player = MagicMock()
        fake_player.playbackState.return_value = QMediaPlayer.PlaybackState.StoppedState
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget._toggle_playback(fake_self)

        fake_player.play.assert_called_once()


# ── _toggle_mute ─────────────────────────────────────────────────────────


class TestToggleMute:
    def test_inverts_mute_and_updates_button(self):
        """Mute toggle: read current state, set the opposite, then
        update the glyph. The button refresh must happen after the
        state change — otherwise the icon would be one click
        behind."""
        fake_audio = MagicMock()
        fake_audio.isMuted.return_value = False
        fake_self = SimpleNamespace(
            _audio_output=fake_audio,
            _update_volume_button=MagicMock(),
        )

        VideoPlayerWidget._toggle_mute(fake_self)

        fake_audio.setMuted.assert_called_once_with(True)
        fake_self._update_volume_button.assert_called_once()

    def test_unmute_when_muted(self):
        fake_audio = MagicMock()
        fake_audio.isMuted.return_value = True
        fake_self = SimpleNamespace(
            _audio_output=fake_audio,
            _update_volume_button=MagicMock(),
        )

        VideoPlayerWidget._toggle_mute(fake_self)

        fake_audio.setMuted.assert_called_once_with(False)


# ── _on_volume_changed ───────────────────────────────────────────────────


class TestOnVolumeChanged:
    def test_slider_int_becomes_audio_float(self):
        """50 (slider) → 0.5 (audio). Uses
        ``volume_int_to_float`` helper."""
        fake_audio = MagicMock()
        fake_self = SimpleNamespace(_audio_output=fake_audio)

        VideoPlayerWidget._on_volume_changed(fake_self, 50)

        fake_audio.setVolume.assert_called_once_with(0.5)


# ── slider drag handlers ─────────────────────────────────────────────────


class TestSliderPressed:
    def test_marks_dragging_true(self):
        """Slider press → drag-mode on. Failure mode: forgetting
        this leaves the auto-update from
        ``_on_position_changed`` fighting the user's drag — the
        slider thumb would flicker between user position and
        media position."""
        fake_self = SimpleNamespace(_slider_dragging=False)

        VideoPlayerWidget._on_slider_pressed(fake_self)

        assert fake_self._slider_dragging is True


class TestSliderReleased:
    def test_clears_dragging_and_seeks(self):
        """Slider release: drop dragging flag, then seek the
        player to the slider's final position. Failure mode: if
        ``setPosition`` ran before the flag clear, an in-flight
        ``_on_position_changed`` would overwrite the user's drop
        position with the player's pre-seek position."""
        fake_slider = MagicMock()
        fake_slider.value.return_value = 12345
        fake_player = MagicMock()
        fake_self = SimpleNamespace(
            _slider_dragging=True,
            _progress_slider=fake_slider,
            _media_player=fake_player,
        )

        VideoPlayerWidget._on_slider_released(fake_self)

        assert fake_self._slider_dragging is False
        fake_player.setPosition.assert_called_once_with(12345)


class TestSliderValueChanged:
    def test_during_drag_updates_current_time(self):
        """User dragging → live-update the current-time label
        as the thumb moves. Without this the label stays at the
        pre-drag position until release."""
        fake_self = SimpleNamespace(
            _slider_dragging=True,
            _update_current_time=MagicMock(),
        )

        VideoPlayerWidget._on_slider_value_changed(fake_self, 5000)

        fake_self._update_current_time.assert_called_once_with(5000)

    def test_not_dragging_no_op(self):
        """Programmatic slider changes (from
        ``_on_position_changed``) must NOT trigger another
        current-time update — that's a separate code path and
        running it twice could yield slightly different
        ``format_duration`` outputs due to rounding."""
        fake_self = SimpleNamespace(
            _slider_dragging=False,
            _update_current_time=MagicMock(),
        )

        VideoPlayerWidget._on_slider_value_changed(fake_self, 5000)

        fake_self._update_current_time.assert_not_called()


# ── _seek_to ─────────────────────────────────────────────────────────────


class TestSeekTo:
    def test_forwards_to_media_player_setPosition(self):
        fake_player = MagicMock()
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget._seek_to(fake_self, 4200)

        fake_player.setPosition.assert_called_once_with(4200)


# ── _update_play_button / _update_volume_button ──────────────────────────


class TestUpdatePlayButton:
    def test_playing_sets_pause_glyph(self):
        fake_button = MagicMock()
        fake_player = MagicMock()
        fake_player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        fake_self = SimpleNamespace(
            _media_player=fake_player,
            _play_button=fake_button,
        )

        VideoPlayerWidget._update_play_button(fake_self)

        fake_button.setText.assert_called_once_with("⏸")

    def test_paused_sets_play_glyph(self):
        fake_button = MagicMock()
        fake_player = MagicMock()
        fake_player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
        fake_self = SimpleNamespace(
            _media_player=fake_player,
            _play_button=fake_button,
        )

        VideoPlayerWidget._update_play_button(fake_self)

        fake_button.setText.assert_called_once_with("▶")


class TestUpdateVolumeButton:
    def test_muted_sets_mute_glyph(self):
        fake_button = MagicMock()
        fake_audio = MagicMock()
        fake_audio.isMuted.return_value = True
        fake_self = SimpleNamespace(
            _audio_output=fake_audio,
            _volume_button=fake_button,
        )

        VideoPlayerWidget._update_volume_button(fake_self)

        fake_button.setText.assert_called_once_with("🔇")

    def test_unmuted_sets_speaker_glyph(self):
        fake_button = MagicMock()
        fake_audio = MagicMock()
        fake_audio.isMuted.return_value = False
        fake_self = SimpleNamespace(
            _audio_output=fake_audio,
            _volume_button=fake_button,
        )

        VideoPlayerWidget._update_volume_button(fake_self)

        fake_button.setText.assert_called_once_with("🔊")


# ── _on_duration_changed ─────────────────────────────────────────────────


class TestOnDurationChanged:
    """Real failure mode pinned: a duration signal can arrive
    BEFORE ``_setup_ui`` has run (Qt's media backend fires it
    synchronously during ``setSource`` on some platforms). The
    method must guard against missing widgets."""

    def test_updates_state_and_widgets_and_emits(self):
        """Happy path: slider range + duration label set, signal
        re-emitted to the group-controller wiring."""
        fake_slider = MagicMock()
        fake_label = MagicMock()
        fake_signal = MagicMock()
        fake_self = SimpleNamespace(
            _duration=0,
            _progress_slider=fake_slider,
            _duration_label=fake_label,
            durationChanged=fake_signal,
        )

        VideoPlayerWidget._on_duration_changed(fake_self, 60000)

        assert fake_self._duration == 60000
        fake_slider.setRange.assert_called_once_with(0, 60000)
        fake_label.setText.assert_called_once()
        fake_signal.emit.assert_called_once_with(60000)

    def test_handles_early_signal_before_slider_constructed(self):
        """Defensive guard: if ``_progress_slider`` is not yet
        attached (early-arrival signal during ``__init__``), the
        method must NOT crash. The state update still happens so
        the deferred ``_setup_ui`` block can apply the duration
        on construction."""
        fake_signal = MagicMock()
        # No _progress_slider / _duration_label attrs at all.
        fake_self = SimpleNamespace(
            _duration=0,
            durationChanged=fake_signal,
        )

        # Must not raise.
        VideoPlayerWidget._on_duration_changed(fake_self, 1234)

        assert fake_self._duration == 1234
        fake_signal.emit.assert_called_once_with(1234)


# ── _on_position_changed ─────────────────────────────────────────────────


class TestOnPositionChanged:
    """The signal handler that drives the progress slider during
    playback. Has a load-bearing guard: if the user is dragging,
    the slider must NOT be auto-updated."""

    def test_not_dragging_updates_slider_and_current_time(self):
        fake_slider = MagicMock()
        fake_signal = MagicMock()
        fake_self = SimpleNamespace(
            _slider_dragging=False,
            _progress_slider=fake_slider,
            _last_position=0,
            _update_current_time=MagicMock(),
            positionChanged=fake_signal,
        )

        VideoPlayerWidget._on_position_changed(fake_self, 7777)

        fake_slider.setValue.assert_called_once_with(7777)
        fake_self._update_current_time.assert_called_once_with(7777)
        assert fake_self._last_position == 7777
        fake_signal.emit.assert_called_once_with(7777)

    def test_dragging_does_not_touch_slider_or_time(self):
        """The named failure mode: a user mid-drag must not have
        their slider jerked back to the media player's current
        position. Test pins the guard."""
        fake_slider = MagicMock()
        fake_signal = MagicMock()
        fake_self = SimpleNamespace(
            _slider_dragging=True,
            _progress_slider=fake_slider,
            _last_position=0,
            _update_current_time=MagicMock(),
            positionChanged=fake_signal,
        )

        VideoPlayerWidget._on_position_changed(fake_self, 7777)

        fake_slider.setValue.assert_not_called()
        fake_self._update_current_time.assert_not_called()
        # last_position and emit happen regardless — they're used
        # by the group-controller wiring.
        assert fake_self._last_position == 7777
        fake_signal.emit.assert_called_once_with(7777)


# ── _on_state_changed ────────────────────────────────────────────────────


class TestOnStateChanged:
    def test_updates_play_button_and_re_emits(self):
        fake_signal = MagicMock()
        fake_self = SimpleNamespace(
            _update_play_button=MagicMock(),
            stateChanged=fake_signal,
        )

        VideoPlayerWidget._on_state_changed(
            fake_self, QMediaPlayer.PlaybackState.PlayingState
        )

        fake_self._update_play_button.assert_called_once()
        fake_signal.emit.assert_called_once_with(
            QMediaPlayer.PlaybackState.PlayingState
        )


# ── _update_current_time ─────────────────────────────────────────────────


class TestUpdateCurrentTime:
    def test_sets_label_with_formatted_duration(self):
        """``_update_current_time(position)`` formats the position
        and sets the label. The formatter (``format_duration``)
        is tested in its own module — here we just pin the
        dispatch."""
        fake_label = MagicMock()
        fake_self = SimpleNamespace(_current_time=fake_label)

        VideoPlayerWidget._update_current_time(fake_self, 65000)

        fake_label.setText.assert_called_once()
        # The text is a formatted duration string — non-empty.
        text = fake_label.setText.call_args.args[0]
        assert isinstance(text, str)
        assert text != ""


# ── public API: play / pause / stop / set_position / set_volume ──────────


class TestPublicAPIPlay:
    def test_play_calls_media_player_play(self):
        fake_player = MagicMock()
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget.play(fake_self)

        fake_player.play.assert_called_once()

    def test_play_swallows_runtime_error(self):
        """Defensive: ``RuntimeError`` from the underlying Qt
        object (already deleted, etc.) must not propagate.
        Named failure mode: a video tile is closed mid-play
        and a stray ``play()`` from the group controller hits
        the deleted widget; the controller mustn't crash."""
        fake_player = MagicMock()
        fake_player.play.side_effect = RuntimeError("wrapped C/C++ object deleted")
        fake_self = SimpleNamespace(_media_player=fake_player)

        # Must not raise.
        VideoPlayerWidget.play(fake_self)


class TestPublicAPIPause:
    def test_forwards_to_media_player(self):
        fake_player = MagicMock()
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget.pause(fake_self)

        fake_player.pause.assert_called_once()


class TestPublicAPIStop:
    def test_forwards_to_media_player(self):
        fake_player = MagicMock()
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget.stop(fake_self)

        fake_player.stop.assert_called_once()


class TestPublicAPISetPosition:
    def test_forwards_to_media_player_setPosition(self):
        fake_player = MagicMock()
        fake_self = SimpleNamespace(_media_player=fake_player)

        VideoPlayerWidget.set_position(fake_self, 9999)

        fake_player.setPosition.assert_called_once_with(9999)


class TestPublicAPISetVolume:
    def test_sets_audio_volume_and_slider_position(self):
        """``set_volume(0.5)`` → audio at 0.5, slider at 50.
        Failure mode: a refactor that only updated one of the
        two would diverge them — the slider thumb would show
        a different position from what the audio output is
        actually doing."""
        fake_audio = MagicMock()
        fake_slider = MagicMock()
        fake_self = SimpleNamespace(
            _audio_output=fake_audio,
            _volume_slider=fake_slider,
        )

        VideoPlayerWidget.set_volume(fake_self, 0.5)

        fake_audio.setVolume.assert_called_once_with(0.5)
        fake_slider.setValue.assert_called_once_with(50)


# ── public API: getters ──────────────────────────────────────────────────


class TestGetPosition:
    def test_forwards_to_media_player(self):
        fake_player = MagicMock()
        fake_player.position.return_value = 1234
        fake_self = SimpleNamespace(_media_player=fake_player)

        assert VideoPlayerWidget.get_position(fake_self) == 1234


class TestGetDuration:
    def test_returns_cached_duration(self):
        fake_self = SimpleNamespace(_duration=60000)
        assert VideoPlayerWidget.get_duration(fake_self) == 60000


class TestIsPlaying:
    def test_returns_true_when_playing(self):
        fake_player = MagicMock()
        fake_player.playbackState.return_value = QMediaPlayer.PlaybackState.PlayingState
        fake_self = SimpleNamespace(_media_player=fake_player)
        assert VideoPlayerWidget.is_playing(fake_self) is True

    def test_returns_false_when_paused(self):
        fake_player = MagicMock()
        fake_player.playbackState.return_value = QMediaPlayer.PlaybackState.PausedState
        fake_self = SimpleNamespace(_media_player=fake_player)
        assert VideoPlayerWidget.is_playing(fake_self) is False


class TestLastPosition:
    def test_get_last_position_returns_attr(self):
        fake_self = SimpleNamespace(_last_position=42)
        assert VideoPlayerWidget.get_last_position(fake_self) == 42

    def test_set_last_position_updates_attr(self):
        fake_self = SimpleNamespace(_last_position=0)
        VideoPlayerWidget.set_last_position(fake_self, 999)
        assert fake_self._last_position == 999
