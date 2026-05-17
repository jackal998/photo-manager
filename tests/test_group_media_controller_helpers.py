"""Tests for :mod:`app.views.widgets.group_media_controller_helpers`.

Covers the pure-logic helpers extracted from
:class:`app.views.widgets.group_media_controller.GroupMediaController`
(#185 / #284). The extraction keeps the load-bearing decision logic
unit-testable against plain Python without cascade-importing the
Qt media stack — same pattern as ``action_handlers.py`` (#182),
``status_reporter_impl.py`` (#138 / #140), ``empty_state.py``
(#137), and ``main_window_helpers.py`` (#185 / #283).

Each test maps to a named real failure mode. Tests whose only
purpose would be hitting defensive branches are deliberately
absent — see CLAUDE.md "Testing ground rules" and
``feedback_no_test_padding``.
"""

from __future__ import annotations

import pytest

from app.views.widgets.group_media_controller_helpers import (
    DEFAULT_UNMUTE_VOLUME,
    compute_master_position,
    compute_mute_target_volume,
    is_majority_playing,
    play_button_icon_for_state,
    should_track_player_position,
    should_update_master_duration,
    volume_icon_for_value,
)


# ── is_majority_playing ───────────────────────────────────────────────────


class TestIsMajorityPlaying:
    """Strict majority-vote (``> total // 2``) — the play-button icon
    flips only when MORE than half the players are playing."""

    def test_zero_players_is_not_playing(self):
        """Empty group → False. Defends against the integer-division
        edge case: ``> 0 // 2 == > 0`` would still return False here,
        but the explicit ``total_players <= 0`` guard makes the
        contract intentional rather than accidental.
        """
        assert is_majority_playing(0, 0) is False

    def test_two_of_three_is_majority(self):
        """3 players, 2 playing → ``2 > 3 // 2 = 1`` → True. The
        common Live Photo case (HEIC + MOV + companion) where the
        button should show 'pause' (⏸)."""
        assert is_majority_playing(2, 3) is True

    def test_one_of_three_is_not_majority(self):
        """3 players, 1 playing → ``1 > 1`` → False. Same group as
        above with only one playing — button should show 'play' (▶).
        """
        assert is_majority_playing(1, 3) is False

    def test_one_of_two_is_not_strict_majority(self):
        """The contentious case: 2 players, 1 playing → ``1 > 1`` →
        False (a tie is NOT a majority). This is the Live Photo
        HEIC+MOV pair where only the MOV plays: the controller
        deliberately shows 'play' so the user can choose to start
        both, not the misleading 'pause' that would suggest
        everything is in motion.

        Failure mode: a refactor to ``>=`` would flip this to True
        and ship the wrong glyph.
        """
        assert is_majority_playing(1, 2) is False

    def test_all_playing_is_majority(self):
        """4 of 4 playing → True. Sanity case."""
        assert is_majority_playing(4, 4) is True

    def test_zero_playing_with_players_registered_is_not_majority(self):
        """5 players, 0 playing → False. The freshly-loaded group."""
        assert is_majority_playing(0, 5) is False


# ── should_update_master_duration ─────────────────────────────────────────


class TestShouldUpdateMasterDuration:
    """The master tracks the LONGEST duration — strict ``>``, never
    ``>=`` or ``!=``."""

    def test_longer_player_updates_master(self):
        """A 5-second MOV joining a group with current master 3s
        (a HEIC's reported 0 or another shorter clip) updates the
        master."""
        assert should_update_master_duration(5000, 3000) is True

    def test_equal_duration_does_not_update(self):
        """Same duration → no update. A refactor to ``>=`` would
        emit redundant range-changes on every duration-changed
        signal from an equal-length player.
        """
        assert should_update_master_duration(3000, 3000) is False

    def test_shorter_player_does_not_update(self):
        """The HEIC (typically duration 0) joining a group whose
        master is already set by an earlier MOV (e.g. 3000ms)
        must NOT shrink the master back down.

        Failure mode: a refactor that always updates would drop
        the slider range to 0 when the HEIC's 0-duration event
        arrives after the MOV's 3000ms event — silently breaking
        slider scrubbing on every Live Photo pair.
        """
        assert should_update_master_duration(0, 3000) is False

    def test_initial_zero_to_nonzero_updates(self):
        """The very first player joining with a non-zero duration
        (5000ms vs initial master 0) updates."""
        assert should_update_master_duration(5000, 0) is True


# ── should_track_player_position ──────────────────────────────────────────


class TestShouldTrackPlayerPosition:
    """The double-negative gate: ``not dragging AND not playing``.
    Only one combination returns True; the other three suppress
    tracking."""

    def test_idle_state_tracks_position(self):
        """Neither dragging nor playing → True (the only way to
        track). This is the "user clicked a different file in the
        grid" case: position events from auto-thumbnail-decode
        update the slider so it reflects where the player is.
        """
        assert should_track_player_position(slider_dragging=False, is_playing=False) is True

    def test_playing_suppresses_tracking(self):
        """Playing → False. Position changes are flooding from the
        decoder; the slider would jump constantly under the user's
        cursor.

        Failure mode: a refactor that uses OR (``not dragging OR
        not playing``) would track during playback — slider jitter
        under the user's hand.
        """
        assert should_track_player_position(slider_dragging=False, is_playing=True) is False

    def test_dragging_suppresses_tracking(self):
        """User dragging the slider → False. The user's input is
        authoritative; the player's position should follow the
        drag, not the other way around."""
        assert should_track_player_position(slider_dragging=True, is_playing=False) is False

    def test_dragging_and_playing_suppresses_tracking(self):
        """Both dragging AND playing → False. The user is scrubbing
        through a playing video; we follow the drag.

        Failure mode: a refactor with ``dragging AND playing`` (no
        ``not``s) would only sync in this specific case — the
        opposite of the contract.
        """
        assert should_track_player_position(slider_dragging=True, is_playing=True) is False


# ── compute_master_position ───────────────────────────────────────────────


class TestComputeMasterPosition:
    """``int(ratio * master_duration)`` with a guard for
    zero-duration (no players or fresh-load state)."""

    def test_half_ratio_returns_half_duration(self):
        """0.5 * 5000ms → 2500ms."""
        assert compute_master_position(0.5, 5000) == 2500

    def test_zero_ratio_returns_zero(self):
        """0.0 * any → 0 (the slider's far-left position)."""
        assert compute_master_position(0.0, 5000) == 0

    def test_one_ratio_returns_full_duration(self):
        """1.0 * 5000 → 5000 (slider's far-right)."""
        assert compute_master_position(1.0, 5000) == 5000

    def test_zero_duration_returns_none(self):
        """No players (or all players reporting 0 duration) → None.
        Caller must skip the ``positionRequested.emit`` to avoid
        Qt no-op-with-warning behaviour on some media backends.

        Failure mode: returning 0 instead of None would silently
        emit position=0 to all players, which on a freshly-loaded
        group would dispatch a useless event before any media is
        actually loaded.
        """
        assert compute_master_position(0.5, 0) is None

    def test_negative_duration_returns_none(self):
        """Defensive: a corrupt master duration (e.g. an
        underflowed counter) returns None rather than a negative
        position."""
        assert compute_master_position(0.5, -100) is None


# ── compute_mute_target_volume ────────────────────────────────────────────


class TestComputeMuteTargetVolume:
    """Toggle: above-zero → 0 (mute), zero → default (un-mute)."""

    def test_above_zero_returns_zero(self):
        """Slider at 50, user clicks mute → return 0."""
        assert compute_mute_target_volume(50) == 0

    def test_at_zero_returns_default(self):
        """Slider at 0, user clicks un-mute → return the default.
        The default is the slider's initial value, NOT the
        user's previous setting (this contract is deliberate —
        see helper docstring)."""
        assert compute_mute_target_volume(0) == DEFAULT_UNMUTE_VOLUME

    def test_default_is_50(self):
        """The default-unmute value matches the slider's initial
        value set in ``_setup_ui`` (50 of 100). If either side
        drifts, the un-mute target won't match what the user sees
        on first launch."""
        assert DEFAULT_UNMUTE_VOLUME == 50

    def test_custom_default_unmute_honoured(self):
        """The default is parametric — callers (e.g. a future
        per-user-preference layer) can override it."""
        assert compute_mute_target_volume(0, default_unmute=75) == 75


# ── volume_icon_for_value ─────────────────────────────────────────────────


class TestVolumeIconForValue:
    """Glyph contract — pin both branches so a designer-driven
    icon swap that misses one side fails at L1."""

    def test_zero_returns_mute_glyph(self):
        assert volume_icon_for_value(0) == "🔇"

    def test_nonzero_returns_speaker_glyph(self):
        assert volume_icon_for_value(50) == "🔊"

    def test_max_returns_speaker_glyph(self):
        assert volume_icon_for_value(100) == "🔊"


# ── play_button_icon_for_state ───────────────────────────────────────────


class TestPlayButtonIconForState:
    """Glyph contract for the play/pause button — the button shows
    the AFFORDANCE (what clicking it will do), not the current state.
    Playing → '⏸' (click to pause); not playing → '▶' (click to
    play). A refactor that inverts these ships the wrong UX
    immediately."""

    def test_playing_shows_pause_glyph(self):
        assert play_button_icon_for_state(True) == "⏸"

    def test_not_playing_shows_play_glyph(self):
        assert play_button_icon_for_state(False) == "▶"
