"""Layer-1 tests for
:class:`app.views.widgets.group_media_controller.GroupMediaController`
(#185 / #284).

Background: ``group_media_controller.py`` had no layer-1 tests for
~6 months — same gap as ``main_window.py`` (closed by #283). This
PR closes the third of the four originally-listed #185 modules
(tree_controller done since #182 / #183 / #228; main_window done
via #283). ``preview_pane.py`` is the only remaining sibling.

## Pattern (mirrors #283)

* **One real-construction test** with the session ``qapp`` fixture
  to catch ``__init__`` / ``_setup_ui`` assembly reorders.
* **Fake-self (``SimpleNamespace``) unbound-method tests** for every
  thin proxy that dispatches signals or routes through extracted
  helpers — instant, no Qt construction overhead.
* The pure-logic decision helpers are tested in
  ``test_group_media_controller_helpers.py`` (sibling module).

## Not covered here (covered elsewhere / by design)

* Real player synchronisation across multiple QMediaPlayer
  instances → layer 3 via s11 (Live Photo scenario).
* Qt signal-slot wiring between controller and players → exercised
  by the construct + register_player tests via mock player
  signals; the real signal dispatch is layer 3 via s11.
* Volume backend behaviour (per-OS QMediaPlayer audio) → not
  unit-testable; covered by manual smoke + s11.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from app.views.widgets.group_media_controller import GroupMediaController


# ── one real-construction test (catches __init__ assembly bugs) ──────────


def test_group_media_controller_constructs_with_qapp(qapp):
    """``GroupMediaController()`` returns without raising and the
    handful of UI attrs the rest of the controller depends on
    are attached.

    Failure mode: a refactor reorders ``_setup_ui`` so a method
    fires before its required widget exists — e.g. ``_toggle_mute``
    can't run until ``_volume_slider`` is attached. The bug surfaces
    as an AttributeError on first user interaction, invisible to
    layer 3 because every Live Photo scenario hits it identically.

    This is the only test in this file that constructs a real
    GroupMediaController. Everything else uses fake-self.
    """
    ctrl = GroupMediaController()
    try:
        # The UI attrs the dispatch methods depend on
        assert ctrl._play_button is not None
        assert ctrl._progress_slider is not None
        assert ctrl._current_time is not None
        assert ctrl._duration_label is not None
        assert ctrl._volume_slider is not None
        assert ctrl._volume_button is not None
        # The state attrs that gate the handlers
        assert ctrl._players == []
        assert ctrl._master_duration == 0
        assert ctrl._is_playing is False
        assert ctrl._slider_dragging is False
        # Sanity: initial volume slider value matches DEFAULT_UNMUTE_VOLUME
        # so the helper's un-mute target lands the user back where they
        # started before muting.
        from app.views.widgets.group_media_controller_helpers import DEFAULT_UNMUTE_VOLUME
        assert ctrl._volume_slider.value() == DEFAULT_UNMUTE_VOLUME
    finally:
        ctrl.deleteLater()


# ── register_player / unregister_player (bridge-pattern class) ───────────


def test_register_player_connects_all_signal_pairs():
    """The full 7-signal connection contract: player→controller (3
    state-tracking signals) + controller→player (4 broadcast slots).

    Failure mode (#175 class): a refactor adds a new broadcast
    signal (e.g. ``seekRequested``) but forgets to ``connect()`` it
    in ``register_player`` — the dispatch silently no-ops on every
    registered player. The same trap as the #175 missing-proxy bug
    that motivated this whole work-stream.
    """
    fake_player = MagicMock()
    fake_self = SimpleNamespace(
        _players=[],
        _on_player_duration_changed=lambda *a: None,
        _on_player_position_changed=lambda *a: None,
        _on_player_state_changed=lambda *a: None,
        playRequested=MagicMock(),
        pauseRequested=MagicMock(),
        positionRequested=MagicMock(),
        volumeRequested=MagicMock(),
    )

    GroupMediaController.register_player(fake_self, fake_player)

    # Player added to the registry exactly once
    assert fake_self._players == [fake_player]

    # Player → controller: 3 callbacks wired
    fake_player.durationChanged.connect.assert_called_once_with(
        fake_self._on_player_duration_changed
    )
    fake_player.positionChanged.connect.assert_called_once_with(
        fake_self._on_player_position_changed
    )
    fake_player.stateChanged.connect.assert_called_once_with(
        fake_self._on_player_state_changed
    )

    # Controller → player: 4 broadcast slots wired
    fake_self.playRequested.connect.assert_called_once_with(fake_player.play)
    fake_self.pauseRequested.connect.assert_called_once_with(fake_player.pause)
    fake_self.positionRequested.connect.assert_called_once_with(fake_player.set_position)
    fake_self.volumeRequested.connect.assert_called_once_with(fake_player.set_volume)


def test_register_player_is_idempotent():
    """Registering the same player twice → no duplicate in the
    registry, no duplicate signal connections. Defends against the
    'why is my player getting double-emit events' class of bug
    (which on QMediaPlayer can manifest as audio playing twice as
    fast or position jittering).
    """
    fake_player = MagicMock()
    fake_self = SimpleNamespace(
        _players=[fake_player],  # already registered
        _on_player_duration_changed=lambda *a: None,
        _on_player_position_changed=lambda *a: None,
        _on_player_state_changed=lambda *a: None,
        playRequested=MagicMock(),
        pauseRequested=MagicMock(),
        positionRequested=MagicMock(),
        volumeRequested=MagicMock(),
    )

    GroupMediaController.register_player(fake_self, fake_player)

    # Registry unchanged, no new connections
    assert fake_self._players == [fake_player]
    fake_player.durationChanged.connect.assert_not_called()
    fake_self.playRequested.connect.assert_not_called()


def test_unregister_player_disconnects_all_signal_pairs():
    """The disconnect-all symmetric to ``register_player``. Failure
    mode (same class as register): a refactor adds a new broadcast
    signal but forgets to ``disconnect`` it here → dead signal
    connections accumulate as users navigate between groups,
    leaking memory and eventually firing the slot on a deleted
    player (Qt RuntimeError)."""
    fake_player = MagicMock()
    fake_self = SimpleNamespace(
        _players=[fake_player],
        _on_player_duration_changed=lambda *a: None,
        _on_player_position_changed=lambda *a: None,
        _on_player_state_changed=lambda *a: None,
        playRequested=MagicMock(),
        pauseRequested=MagicMock(),
        positionRequested=MagicMock(),
        volumeRequested=MagicMock(),
    )

    GroupMediaController.unregister_player(fake_self, fake_player)

    assert fake_self._players == []

    # All 3 player-side disconnects called
    fake_player.durationChanged.disconnect.assert_called_once()
    fake_player.positionChanged.disconnect.assert_called_once()
    fake_player.stateChanged.disconnect.assert_called_once()

    # All 4 controller-side disconnects called
    fake_self.playRequested.disconnect.assert_called_once()
    fake_self.pauseRequested.disconnect.assert_called_once()
    fake_self.positionRequested.disconnect.assert_called_once()
    fake_self.volumeRequested.disconnect.assert_called_once()


def test_unregister_player_swallows_disconnect_exceptions():
    """Disconnect on an already-disconnected signal raises
    ``TypeError`` or ``RuntimeError`` (depending on PySide6
    version). The controller must swallow these so a partial
    re-registration doesn't leave the user unable to clean up the
    group.

    Failure mode: a refactor that drops the try/except (or narrows
    the except clauses) would crash ``cleanup()`` on a group
    navigation, leaving the player widgets stranded with a dead
    controller.
    """
    fake_player = MagicMock()
    # Make every disconnect raise — simulates already-disconnected state
    fake_player.durationChanged.disconnect.side_effect = TypeError("already disconnected")
    fake_player.positionChanged.disconnect.side_effect = RuntimeError("dead signal")

    fake_self = SimpleNamespace(
        _players=[fake_player],
        _on_player_duration_changed=lambda *a: None,
        _on_player_position_changed=lambda *a: None,
        _on_player_state_changed=lambda *a: None,
        playRequested=MagicMock(),
        pauseRequested=MagicMock(),
        positionRequested=MagicMock(),
        volumeRequested=MagicMock(),
    )

    # Must not raise
    GroupMediaController.unregister_player(fake_self, fake_player)

    # Player still removed despite the disconnect errors
    assert fake_self._players == []


def test_unregister_player_is_noop_for_non_registered():
    """Unregistering a player that was never registered → no-op, no
    crash. Defends the `cleanup()` path against any caller that
    accidentally passes a stranger.
    """
    fake_player = MagicMock()
    fake_self = SimpleNamespace(_players=[])

    # Must not raise
    GroupMediaController.unregister_player(fake_self, fake_player)

    fake_player.durationChanged.disconnect.assert_not_called()


# ── cleanup ──────────────────────────────────────────────────────────────


def test_cleanup_unregisters_every_player():
    """``cleanup()`` walks the player list and unregisters each one,
    then clears the list. Failure mode: a refactor that mutates
    ``_players`` mid-iteration (forgetting the ``list(...)`` copy)
    would skip half the players, leaking signal connections.
    """
    p1 = MagicMock()
    p2 = MagicMock()
    p3 = MagicMock()
    unreg_calls: list = []

    def fake_unregister(self, player):
        unreg_calls.append(player)
        self._players.remove(player)

    fake_self = SimpleNamespace(
        _players=[p1, p2, p3],
        unregister_player=lambda player: fake_unregister(fake_self, player),
    )

    GroupMediaController.cleanup(fake_self)

    assert unreg_calls == [p1, p2, p3]
    assert fake_self._players == []


# ── _toggle_playback (dispatches play or pause based on state) ───────────


def test_toggle_playback_when_not_playing_emits_play():
    """Currently paused → emit ``playRequested``. Pinned because
    the play-button click is the most-common interaction with the
    group controller; a refactor that flips the branch silently
    swaps play and pause."""
    fake_self = SimpleNamespace(
        _is_playing=False,
        playRequested=MagicMock(),
        pauseRequested=MagicMock(),
    )

    GroupMediaController._toggle_playback(fake_self)

    fake_self.playRequested.emit.assert_called_once_with()
    fake_self.pauseRequested.emit.assert_not_called()


def test_toggle_playback_when_playing_emits_pause():
    """Currently playing → emit ``pauseRequested``."""
    fake_self = SimpleNamespace(
        _is_playing=True,
        playRequested=MagicMock(),
        pauseRequested=MagicMock(),
    )

    GroupMediaController._toggle_playback(fake_self)

    fake_self.pauseRequested.emit.assert_called_once_with()
    fake_self.playRequested.emit.assert_not_called()


# ── _toggle_mute / _on_volume_changed (helper-backed) ────────────────────


def test_toggle_mute_with_volume_above_zero_sets_to_zero():
    """The mute path: slider above zero → set slider to 0. The
    helper computes the target; this test pins that the method
    routes through it correctly."""
    fake_slider = MagicMock()
    fake_slider.value.return_value = 50
    fake_self = SimpleNamespace(_volume_slider=fake_slider)

    GroupMediaController._toggle_mute(fake_self)

    fake_slider.setValue.assert_called_once_with(0)


def test_toggle_mute_with_volume_at_zero_sets_to_default():
    """The un-mute path: slider at 0 → set slider to default (50)."""
    from app.views.widgets.group_media_controller_helpers import DEFAULT_UNMUTE_VOLUME

    fake_slider = MagicMock()
    fake_slider.value.return_value = 0
    fake_self = SimpleNamespace(_volume_slider=fake_slider)

    GroupMediaController._toggle_mute(fake_self)

    fake_slider.setValue.assert_called_once_with(DEFAULT_UNMUTE_VOLUME)


def test_on_volume_changed_emits_normalized_volume():
    """Slider value (0-100) → emit as normalized 0.0-1.0 float.
    Real failure mode: a refactor that forgets the ``/100.0`` would
    emit volume=50 to QMediaPlayer (which clamps to 1.0, i.e. max
    volume) — every slider drag goes to max."""
    fake_self = SimpleNamespace(
        _update_volume_button=MagicMock(),
        volumeRequested=MagicMock(),
    )

    GroupMediaController._on_volume_changed(fake_self, 75)

    fake_self.volumeRequested.emit.assert_called_once_with(0.75)
    fake_self._update_volume_button.assert_called_once_with()


# ── slider drag state (the press / release / value_changed trio) ─────────


def test_on_slider_pressed_sets_drag_flag():
    """Slider press → ``_slider_dragging = True``. This flag gates
    whether player-position-change events update the slider (see
    ``should_track_player_position`` helper)."""
    fake_self = SimpleNamespace(_slider_dragging=False)

    GroupMediaController._on_slider_pressed(fake_self)

    assert fake_self._slider_dragging is True


def test_on_slider_released_clears_flag_and_emits_position():
    """Slider release → clear drag flag AND emit current value as
    the new desired position. Real failure mode: a refactor that
    forgets the emit would leave the user's seek intent
    un-broadcast — the slider snaps back to whatever the player
    reports next."""
    fake_slider = MagicMock()
    fake_slider.value.return_value = 2500
    fake_self = SimpleNamespace(
        _slider_dragging=True,
        _progress_slider=fake_slider,
        positionRequested=MagicMock(),
    )

    GroupMediaController._on_slider_released(fake_self)

    assert fake_self._slider_dragging is False
    fake_self.positionRequested.emit.assert_called_once_with(2500)


def test_on_slider_value_changed_updates_time_only_when_dragging():
    """During drag, value changes update the current-time display
    so the user sees the seek target. Without drag, value changes
    arrive from player-state-updates and the time text is updated
    by ``_on_player_position_changed`` instead."""
    update_calls: list = []
    fake_self = SimpleNamespace(
        _slider_dragging=True,
        _update_current_time=lambda v: update_calls.append(v),
    )

    GroupMediaController._on_slider_value_changed(fake_self, 1234)

    assert update_calls == [1234]


def test_on_slider_value_changed_noop_when_not_dragging():
    """Programmatic value changes (e.g. from
    ``_on_player_position_changed``) must not double-update the
    time display — that path updates it directly."""
    update_calls: list = []
    fake_self = SimpleNamespace(
        _slider_dragging=False,
        _update_current_time=lambda v: update_calls.append(v),
    )

    GroupMediaController._on_slider_value_changed(fake_self, 1234)

    assert update_calls == []


# ── _on_player_* handlers (helper-backed) ────────────────────────────────


def test_on_player_duration_changed_updates_master_when_longer():
    """A longer player → update master_duration + slider range +
    label. The helper makes the decision; this test pins that the
    side-effects (range + label) happen on the True path."""
    fake_slider = MagicMock()
    fake_label = MagicMock()
    fake_self = SimpleNamespace(
        _master_duration=3000,
        _progress_slider=fake_slider,
        _duration_label=fake_label,
    )

    GroupMediaController._on_player_duration_changed(fake_self, 5000)

    assert fake_self._master_duration == 5000
    fake_slider.setRange.assert_called_once_with(0, 5000)
    fake_label.setText.assert_called_once()


def test_on_player_duration_changed_skips_update_when_not_longer():
    """A shorter (or equal) player → no side-effects. Defends the
    'shorter HEIC after longer MOV' regression mentioned in the
    helper test."""
    fake_slider = MagicMock()
    fake_label = MagicMock()
    fake_self = SimpleNamespace(
        _master_duration=5000,
        _progress_slider=fake_slider,
        _duration_label=fake_label,
    )

    GroupMediaController._on_player_duration_changed(fake_self, 1000)

    assert fake_self._master_duration == 5000  # unchanged
    fake_slider.setRange.assert_not_called()
    fake_label.setText.assert_not_called()


def test_on_player_position_changed_updates_when_idle():
    """Idle (not dragging, not playing) → slider syncs to player.
    The composite of helper + side-effects in one test."""
    fake_slider = MagicMock()
    update_calls: list = []
    fake_self = SimpleNamespace(
        _slider_dragging=False,
        _is_playing=False,
        _master_position=0,
        _progress_slider=fake_slider,
        _update_current_time=lambda v: update_calls.append(v),
    )

    GroupMediaController._on_player_position_changed(fake_self, 1500)

    assert fake_self._master_position == 1500
    fake_slider.setValue.assert_called_once_with(1500)
    assert update_calls == [1500]


def test_on_player_position_changed_skipped_when_playing():
    """Playing → don't touch the slider. Real failure mode (#239
    territory cousin): visible slider jitter during playback."""
    fake_slider = MagicMock()
    fake_self = SimpleNamespace(
        _slider_dragging=False,
        _is_playing=True,
        _master_position=0,
        _progress_slider=fake_slider,
        _update_current_time=MagicMock(),
    )

    GroupMediaController._on_player_position_changed(fake_self, 1500)

    fake_slider.setValue.assert_not_called()
    fake_self._update_current_time.assert_not_called()


def test_on_player_state_changed_updates_is_playing_via_majority_helper():
    """Player state-change → recount via majority helper → update
    ``_is_playing`` + refresh button. The integration with
    ``is_majority_playing`` and the .is_playing() probe per player."""
    p1 = MagicMock()
    p1.is_playing.return_value = True
    p2 = MagicMock()
    p2.is_playing.return_value = True
    p3 = MagicMock()
    p3.is_playing.return_value = False

    fake_self = SimpleNamespace(
        _players=[p1, p2, p3],
        _is_playing=False,
        _update_play_button=MagicMock(),
    )

    GroupMediaController._on_player_state_changed(fake_self, "any_state")

    # 2 of 3 playing → majority → True
    assert fake_self._is_playing is True
    fake_self._update_play_button.assert_called_once_with()


# ── _update_play_button / _update_volume_button (helper-backed) ──────────


def test_update_play_button_uses_helper():
    """Pins that the method routes through the icon helper. Pairs
    with ``test_play_button_icon_for_state_*`` to cover both branches.
    """
    fake_button = MagicMock()
    fake_self = SimpleNamespace(_is_playing=True, _play_button=fake_button)

    GroupMediaController._update_play_button(fake_self)

    fake_button.setText.assert_called_once_with("⏸")


def test_update_volume_button_uses_helper():
    """Same shape for the volume button."""
    fake_slider = MagicMock()
    fake_slider.value.return_value = 0
    fake_button = MagicMock()
    fake_self = SimpleNamespace(_volume_slider=fake_slider, _volume_button=fake_button)

    GroupMediaController._update_volume_button(fake_self)

    fake_button.setText.assert_called_once_with("🔇")


# ── public API: get_registered_count / set_position_sync ─────────────────


def test_get_registered_count_returns_player_list_length():
    """Trivial accessor; pins the API for callers that gate UI on
    'are there any players to control'."""
    fake_self = SimpleNamespace(_players=[MagicMock(), MagicMock(), MagicMock()])

    assert GroupMediaController.get_registered_count(fake_self) == 3


def test_set_position_sync_emits_helper_result():
    """Public seek API — ratio → position via helper → broadcast.
    Failure mode: a refactor that drops the helper's None guard
    would emit position=0 on a zero-duration group, which on some
    backends logs a warning per player."""
    fake_self = SimpleNamespace(
        _master_duration=5000,
        positionRequested=MagicMock(),
    )

    GroupMediaController.set_position_sync(fake_self, 0.5)

    fake_self.positionRequested.emit.assert_called_once_with(2500)


def test_set_position_sync_skips_emit_when_duration_zero():
    """Zero master duration → helper returns None → no emit."""
    fake_self = SimpleNamespace(
        _master_duration=0,
        positionRequested=MagicMock(),
    )

    GroupMediaController.set_position_sync(fake_self, 0.5)

    fake_self.positionRequested.emit.assert_not_called()
