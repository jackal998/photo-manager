// Unit tests for groupMediaSync.ts — the TS port of the Qt
// group_media_controller_helpers.py contract.
//
// Each test case maps to a named real failure mode documented in the Qt
// source and its tests/test_group_media_controller_helpers.py counterpart.
// Tests that only hit defensive branches (metric gaming) are absent per
// CLAUDE.md testing ground rules.

import { describe, it, expect } from "vitest";
import {
  DEFAULT_UNMUTE_VOLUME,
  computeMasterPosition,
  computeMuteTargetVolume,
  isMajorityPlaying,
  playButtonIconForState,
  shouldTrackPlayerPosition,
  shouldUpdateMasterDuration,
  volumeIconForValue,
} from "./groupMediaSync";

// ---------------------------------------------------------------------------
// isMajorityPlaying
// ---------------------------------------------------------------------------

describe("isMajorityPlaying", () => {
  it("returns false for zero players — empty group can't be playing", () => {
    expect(isMajorityPlaying(0, 0)).toBe(false);
  });

  it("returns true for 2-of-3 — the common Live Photo case (HEIC+MOV+companion)", () => {
    // 2 > Math.floor(3/2) = 1 → true
    expect(isMajorityPlaying(2, 3)).toBe(true);
  });

  it("returns false for 1-of-3 — not a majority, button stays play (▶)", () => {
    // 1 > 1 → false
    expect(isMajorityPlaying(1, 3)).toBe(false);
  });

  it("returns false for 1-of-2 — tie is NOT a majority (Live Photo HEIC+MOV where only MOV plays)", () => {
    // Failure mode: using >= instead of > would flip this to true and ship the wrong glyph.
    // 1 > Math.floor(2/2) = 1 → false
    expect(isMajorityPlaying(1, 2)).toBe(false);
  });

  it("returns true for 4-of-4 — all playing", () => {
    expect(isMajorityPlaying(4, 4)).toBe(true);
  });

  it("returns false for 0-of-5 — freshly-loaded group", () => {
    expect(isMajorityPlaying(0, 5)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// shouldUpdateMasterDuration
// ---------------------------------------------------------------------------

describe("shouldUpdateMasterDuration", () => {
  it("returns true when new duration is longer (master should update)", () => {
    // A 5s MOV joining a group with 3s master updates the master.
    expect(shouldUpdateMasterDuration(5000, 3000)).toBe(true);
  });

  it("returns false for equal duration — avoids redundant range-change emissions", () => {
    // Failure mode: >= would emit spurious events from equal-length players.
    expect(shouldUpdateMasterDuration(3000, 3000)).toBe(false);
  });

  it("returns false when new is shorter — HEIC (0) must NOT shrink the MOV master", () => {
    // Failure mode: always-update would drop slider range to 0 when HEIC's
    // 0-duration event arrives after the MOV's 3000ms event.
    expect(shouldUpdateMasterDuration(0, 3000)).toBe(false);
  });

  it("returns true from zero to nonzero — first player joining with a duration", () => {
    expect(shouldUpdateMasterDuration(5000, 0)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// shouldTrackPlayerPosition
// ---------------------------------------------------------------------------

describe("shouldTrackPlayerPosition", () => {
  it("returns true when idle (not dragging, not playing) — the only sync state", () => {
    expect(shouldTrackPlayerPosition(false, false)).toBe(true);
  });

  it("returns false when playing — slider would jump constantly under the cursor", () => {
    // Failure mode: OR instead of AND would track during playback.
    expect(shouldTrackPlayerPosition(false, true)).toBe(false);
  });

  it("returns false when dragging — user's drag is authoritative", () => {
    expect(shouldTrackPlayerPosition(true, false)).toBe(false);
  });

  it("returns false when dragging AND playing — user scrubbing through live video", () => {
    // Failure mode: 'dragging && playing' (no nots) would invert the contract.
    expect(shouldTrackPlayerPosition(true, true)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// computeMasterPosition
// ---------------------------------------------------------------------------

describe("computeMasterPosition", () => {
  it("returns half the duration for ratio 0.5", () => {
    expect(computeMasterPosition(0.5, 5000)).toBe(2500);
  });

  it("returns 0 for ratio 0.0 (slider far-left)", () => {
    expect(computeMasterPosition(0.0, 5000)).toBe(0);
  });

  it("returns full duration for ratio 1.0 (slider far-right)", () => {
    expect(computeMasterPosition(1.0, 5000)).toBe(5000);
  });

  it("returns null for zero duration — no players registered yet", () => {
    // Failure mode: returning 0 instead of null would silently emit position=0
    // to all players before any media is loaded.
    expect(computeMasterPosition(0.5, 0)).toBeNull();
  });

  it("returns null for negative duration — defensive guard for corrupt master", () => {
    expect(computeMasterPosition(0.5, -100)).toBeNull();
  });

  it("truncates (not rounds) fractional ms", () => {
    // int() in Python truncates; Math.trunc() in TS matches.
    expect(computeMasterPosition(0.333, 1000)).toBe(333);
  });
});

// ---------------------------------------------------------------------------
// computeMuteTargetVolume
// ---------------------------------------------------------------------------

describe("computeMuteTargetVolume", () => {
  it("returns 0 when current volume is above 0 (mute)", () => {
    expect(computeMuteTargetVolume(0.5)).toBe(0);
  });

  it("returns DEFAULT_UNMUTE_VOLUME when current volume is 0 (un-mute)", () => {
    // The default is a fixed snap-to value, NOT the user's previous setting.
    expect(computeMuteTargetVolume(0)).toBe(DEFAULT_UNMUTE_VOLUME);
  });

  it("DEFAULT_UNMUTE_VOLUME is 0.5 (matches Qt's 50/100 initial slider value)", () => {
    // If either side drifts, un-mute target won't match what the user sees on launch.
    expect(DEFAULT_UNMUTE_VOLUME).toBe(0.5);
  });

  it("honours a custom defaultUnmute — parametric for future per-user-preference layer", () => {
    expect(computeMuteTargetVolume(0, 0.75)).toBe(0.75);
  });

  it("mutes from maximum volume", () => {
    expect(computeMuteTargetVolume(1.0)).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// volumeIconForValue
// ---------------------------------------------------------------------------

describe("volumeIconForValue", () => {
  it("returns mute glyph for volume === 0", () => {
    expect(volumeIconForValue(0)).toBe("🔇");
  });

  it("returns speaker glyph for volume > 0", () => {
    expect(volumeIconForValue(0.5)).toBe("🔊");
  });

  it("returns speaker glyph for maximum volume 1", () => {
    expect(volumeIconForValue(1)).toBe("🔊");
  });
});

// ---------------------------------------------------------------------------
// playButtonIconForState
// ---------------------------------------------------------------------------

describe("playButtonIconForState", () => {
  it("shows pause glyph when playing (the 'click to pause' affordance)", () => {
    // Failure mode: inverting the conditional ships '▶' while audio runs.
    expect(playButtonIconForState(true)).toBe("⏸");
  });

  it("shows play glyph when not playing (the 'click to play' affordance)", () => {
    // Failure mode: inverting ships '⏸' when nothing is playing.
    expect(playButtonIconForState(false)).toBe("▶");
  });
});
