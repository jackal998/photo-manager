// Pure-logic helpers for the web port of GroupMediaController.
//
// Verbatim port of app/views/widgets/group_media_controller_helpers.py.
// Names camelCased; logic identical — the Qt unit tests in
// tests/test_group_media_controller_helpers.py are the source of truth.
//
// These functions are dependency-free and have no DOM or React imports so
// they can be tested in a plain Vitest Node environment.

/**
 * Default volume (0..1 scale) when the user un-mutes a player whose
 * slider sat at zero. 0.5 matches the Qt DEFAULT_UNMUTE_VOLUME=50 / 100.
 *
 * Note: the Qt controller works in integer 0..100; the web works in
 * floating-point 0..1 (HTML <video>.volume). The tests pin the ratio.
 */
export const DEFAULT_UNMUTE_VOLUME = 0.5;

// ---------------------------------------------------------------------------
// is_majority_playing
// ---------------------------------------------------------------------------

/**
 * Return true when more than half of the registered players are currently
 * playing (strict ``playing > total // 2``).
 *
 * - total === 0 → false (empty group can't be "playing").
 * - tie (1 of 2) → false: equal split is NOT a majority.
 *
 * Drives the group play-button glyph: shows "pause" only when genuinely
 * more than half are playing.
 *
 * Failure mode: using >= instead of > would flip the button on a 1-of-2
 * pair (Live Photo HEIC+MOV) where only the MOV plays.
 */
export function isMajorityPlaying(
  playingCount: number,
  totalPlayers: number
): boolean {
  if (totalPlayers <= 0) return false;
  return playingCount > Math.floor(totalPlayers / 2);
}

// ---------------------------------------------------------------------------
// should_update_master_duration
// ---------------------------------------------------------------------------

/**
 * Return true when ``newDuration`` should replace the stored master duration.
 *
 * Strict ``>`` — the master always tracks the LONGEST duration seen across
 * all registered players.
 *
 * Failure mode: using ``>=`` emits redundant range-changes from equal-length
 * players; using ``!=`` would shrink the master when a short player reports
 * last, breaking slider range on groups with unequal-length clips.
 */
export function shouldUpdateMasterDuration(
  newDuration: number,
  currentMaster: number
): boolean {
  return newDuration > currentMaster;
}

// ---------------------------------------------------------------------------
// should_track_player_position
// ---------------------------------------------------------------------------

/**
 * Return true when the controller should sync its slider/timestamp to a
 * position-change event from a player.
 *
 * Contract: ``!dragging && !isPlaying`` — sync only when idle AND the user
 * is not drag-scrubbing.
 *
 * Failure modes:
 * - ``!dragging || !isPlaying`` — slider updates during playback, jumping
 *   under the cursor as the player advances.
 * - ``dragging && isPlaying`` — fights the user's drag while playing,
 *   producing slider jitter.
 */
export function shouldTrackPlayerPosition(
  sliderDragging: boolean,
  isPlaying: boolean
): boolean {
  return !sliderDragging && !isPlaying;
}

// ---------------------------------------------------------------------------
// compute_master_position
// ---------------------------------------------------------------------------

/**
 * Return the absolute position (in ms) for a slider ratio in [0.0, 1.0].
 *
 * Returns ``null`` when ``masterDuration <= 0`` (no players registered yet,
 * or all players report 0 duration). Callers must skip the seek emit when
 * null is returned — seeking on a zero-duration player is a no-op on some
 * media backends and produces warnings.
 */
export function computeMasterPosition(
  positionRatio: number,
  masterDuration: number
): number | null {
  if (masterDuration <= 0) return null;
  return Math.trunc(positionRatio * masterDuration);
}

// ---------------------------------------------------------------------------
// compute_mute_target_volume
// ---------------------------------------------------------------------------

/**
 * Return the volume to set when the user toggles mute.
 *
 * - current > 0 → mute: return 0.
 * - current === 0 → un-mute: return ``defaultUnmute`` (DEFAULT_UNMUTE_VOLUME).
 *
 * The un-mute target is a fixed default, NOT the user's pre-mute value
 * (deliberate — see Qt helper docstring). ``defaultUnmute`` is in the same
 * 0..1 scale as HTML <video>.volume.
 */
export function computeMuteTargetVolume(
  currentVolume: number,
  defaultUnmute: number = DEFAULT_UNMUTE_VOLUME
): number {
  if (currentVolume > 0) return 0;
  return defaultUnmute;
}

// ---------------------------------------------------------------------------
// volume_icon_for_value
// ---------------------------------------------------------------------------

/**
 * Return the glyph the volume button shows for ``value`` (0..1 scale).
 *
 * "🔇" when volume is exactly 0, "🔊" otherwise.
 *
 * Failure mode: a renamed glyph that misses one branch ships with a wrong
 * icon mid-state. The unit test pins the contract so a refactor that swaps
 * the two fails immediately.
 */
export function volumeIconForValue(value: number): string {
  return value === 0 ? "🔇" : "🔊";
}

// ---------------------------------------------------------------------------
// play_button_icon_for_state
// ---------------------------------------------------------------------------

/**
 * Return the glyph the play/pause button shows for the aggregate playing state.
 *
 * "⏸" when playing (shows the "click to pause" affordance), "▶" otherwise.
 *
 * Failure mode: inverting the conditional ships the wrong glyph in both
 * states — button reads "pause" when nothing plays, "play" while audio runs.
 */
export function playButtonIconForState(isPlaying: boolean): string {
  return isPlaying ? "⏸" : "▶";
}
