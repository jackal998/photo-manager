"""Pure-logic helpers extracted from
:class:`app.views.widgets.group_media_controller.GroupMediaController`.

Extracted so the load-bearing decision logic (majority-vote
play-state, master-duration maximisation, drag-vs-playback gating,
mute-toggle target volume, icon resolution) is unit-testable
against plain Python without cascade-importing the Qt
``QMediaPlayer`` / ``QVideoWidget`` stack.

Same extraction pattern previously used by ``action_handlers.py``
(#182), ``status_reporter_impl.py`` (#138, #140), ``empty_state.py``
(#137), and ``main_window_helpers.py`` (#185 / #283).

What lives here:

* :func:`is_majority_playing` — majority-vote on whether the group
  should report itself as "playing" given per-player state.
* :func:`should_update_master_duration` — guard for the master
  duration becoming the MAX across all registered players (used
  when an unequal-length player joins, e.g. Live Photo HEIC+MOV
  with a longer MOV).
* :func:`should_track_player_position` — double-negative gate the
  position callback uses to avoid fighting an active user drag or
  jumping the slider mid-playback.
* :func:`compute_master_position` — slider-ratio → absolute
  position arithmetic for the public sync API.
* :func:`compute_mute_target_volume` — toggle between
  current-volume-or-0 and 0-or-default.
* :func:`volume_icon_for_value` / :func:`play_button_icon_for_state` —
  the two glyph-resolution helpers; tiny but real (a renamed glyph
  is the kind of silent UX regression a snapshot test would never
  catch).
"""

from __future__ import annotations

#: Default volume when the user un-mutes a player whose slider sat
#: at zero. 50 (of 100) matches the slider's initial value set in
#: :meth:`GroupMediaController._setup_ui`.
DEFAULT_UNMUTE_VOLUME: int = 50


def is_majority_playing(playing_count: int, total_players: int) -> bool:
    """Return True when more than half of the registered players are
    currently playing.

    Uses ``playing_count > total // 2`` (strict). For ``total == 0``
    the result is False — an empty group can't be "playing". For
    even ``total`` this means equal split (2 of 4) reports False,
    which matches the design intent: the group's play-button shows
    "pause" (▶) only when the majority is genuinely playing.

    Pulled from :meth:`GroupMediaController._on_player_state_changed`.
    Failure mode: a refactor that uses ``>=`` instead would flip the
    button on 1-of-2 playing (visible as the wrong icon on a Live
    Photo HEIC+MOV pair where only the MOV plays).
    """
    if total_players <= 0:
        return False
    return playing_count > total_players // 2


def should_update_master_duration(new_duration: int, current_master: int) -> bool:
    """Return True when the new duration should replace the stored
    master duration.

    Strict ``>`` is intentional — only a STRICTLY longer player
    updates the master, so the master always tracks the LONGEST
    duration seen. Pulled from
    :meth:`GroupMediaController._on_player_duration_changed`.

    Failure mode: a refactor that always updates (or uses ``!=``)
    would track the LAST-reported duration instead of the max,
    breaking the slider range for any group with players of unequal
    length (Live Photo MOV is typically 3-5s; the HEIC stays at 0).
    """
    return new_duration > current_master


def should_track_player_position(slider_dragging: bool, is_playing: bool) -> bool:
    """Return True when the controller should sync its slider /
    timestamp to a position-change event from a player.

    The double-negative ``not dragging AND not playing`` is the
    contract: sync only when we're idle AND the user isn't
    drag-scrubbing. Pulled from
    :meth:`GroupMediaController._on_player_position_changed`.

    Failure modes both involve flipping the guard:
      * ``not dragging OR not playing`` — slider would still update
        during playback, jumping under the cursor as the player
        advances.
      * ``dragging AND playing`` — would fight the user's drag while
        the player is playing, producing slider jitter.
    """
    return not slider_dragging and not is_playing


def compute_master_position(position_ratio: float, master_duration: int) -> int | None:
    """Return the absolute position (in ms) for a slider ratio in
    ``[0.0, 1.0]``.

    Returns ``None`` when the master duration is non-positive (no
    players registered yet, or all players still report 0 duration)
    — callers must skip the emit when ``None`` is returned, since
    seeking to 0 on a zero-duration player is a Qt no-op that some
    backends turn into an error log.

    Pulled from :meth:`GroupMediaController.set_position_sync`.
    """
    if master_duration <= 0:
        return None
    return int(position_ratio * master_duration)


def compute_mute_target_volume(
    current_volume: int, default_unmute: int = DEFAULT_UNMUTE_VOLUME
) -> int:
    """Return the volume to set when the user toggles mute.

    Contract: if the slider is currently above 0, mute → set to 0.
    If currently at 0, un-mute → restore to ``default_unmute`` (50
    by default, matching the slider's initial value).

    Pulled from :meth:`GroupMediaController._toggle_mute`.

    Failure mode: a refactor that "remembers" the previous non-zero
    volume (capturing pre-mute state) would change the un-mute
    behaviour from "snap to default" to "restore last" — a real UX
    shift the user would notice as their volume sliding around
    after each mute toggle.
    """
    if current_volume > 0:
        return 0
    return default_unmute


def volume_icon_for_value(value: int) -> str:
    """Return the glyph the volume button shows for ``value``.

    ``"🔇"`` when volume is exactly 0, ``"🔊"`` otherwise. Pulled
    from :meth:`GroupMediaController._update_volume_button`.

    Failure mode: a renamed glyph (a designer-driven copy change
    that misses one branch) ships with a wrong icon mid-state. The
    unit test pins the contract so a refactor that swaps the two
    fails layer 1 immediately.
    """
    return "🔇" if value == 0 else "🔊"


def play_button_icon_for_state(is_playing: bool) -> str:
    """Return the glyph the play/pause button shows for the given
    aggregate playing state.

    ``"⏸"`` when playing (shows the "click to pause" affordance),
    ``"▶"`` otherwise. Pulled from
    :meth:`GroupMediaController._update_play_button`.

    Failure mode: a refactor that inverts the conditional ships
    the wrong glyph in both states — the button reads "pause" when
    nothing is playing, "play" while audio is going.
    """
    return "⏸" if is_playing else "▶"
