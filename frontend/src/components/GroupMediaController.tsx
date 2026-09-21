// GroupMediaController — synchronized-playback control bar for a grid of
// video tiles.
//
// Mirrors app/views/widgets/group_media_controller.py EXACTLY:
//   - Play/pause button (majority-vote via isMajorityPlaying)
//   - Master progress slider ranged to the longest-duration player
//     (shouldUpdateMasterDuration); seeks all on RELEASE (seekAll)
//   - Current-time + duration labels
//   - Shared volume slider 0-100 (default 50) -> setVolumeAll(v/100)
//   - Mute button toggles 0<->50 (computeMuteTargetVolume on 0..1 scale)
//   - Listens to every registered <video>'s timeupdate / durationchange /
//     play / pause events; handles live player mount/unmount via subscribe()
//   - Threshold group-autoplay: fires playAll() once when every video in the
//     group has registered, guarded by a ref so it fires only once per grid
//     load (not per re-render / React Strict Mode double-invoke)
//
// Contract: must be rendered INSIDE a <GroupMediaProvider>. The parent
// supplies `expectedPlayerCount` — the total number of video tiles in the
// group — so the autoplay guard can fire at the right moment.

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useGroupMedia } from "@/hooks/useGroupMedia";
import {
  isMajorityPlaying,
  shouldUpdateMasterDuration,
  shouldTrackPlayerPosition,
  computeMasterPosition,
  computeMuteTargetVolume,
  volumeIconForValue,
  playButtonIconForState,
  DEFAULT_UNMUTE_VOLUME,
} from "@/lib/groupMediaSync";
import {
  GMC_BAR,
  GMC_PLAY_PAUSE,
  GMC_PROGRESS_SLIDER,
  GMC_CURRENT_TIME,
  GMC_DURATION,
  GMC_MUTE_BUTTON,
  GMC_VOLUME_SLIDER,
} from "@/testids";

// Re-export for callers that were importing the local constants before the
// Integrate phase moved them into the canonical testid pipeline.
export { GMC_BAR, GMC_PLAY_PAUSE, GMC_PROGRESS_SLIDER, GMC_CURRENT_TIME, GMC_DURATION, GMC_MUTE_BUTTON, GMC_VOLUME_SLIDER };

// ---------------------------------------------------------------------------
// formatDuration — local port of app/views/media_utils.py:format_duration
// "if present" was not satisfied: no formatDuration in frontend/src/lib/format.ts
// ---------------------------------------------------------------------------

function formatDuration(ms: number): string {
  if (ms < 0) return "--:--";
  const totalSeconds = Math.trunc(ms / 1000);
  const hours = Math.trunc(totalSeconds / 3600);
  const minutes = Math.trunc((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${mm}:${ss}`;
  }
  return `${mm}:${ss}`;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface GroupMediaControllerProps {
  /**
   * Total number of video tiles in the group. The controller fires the
   * threshold group-autoplay exactly once when the registry reaches this count.
   * Defaults to 0 (autoplay disabled). Callers (GroupGrid) should supply the
   * count of video tiles in the group; when not provided the autoplay guard
   * never fires.
   */
  expectedPlayerCount?: number;
}

// ---------------------------------------------------------------------------
// GroupMediaController
// ---------------------------------------------------------------------------

export function GroupMediaController({
  expectedPlayerCount = 0,
}: GroupMediaControllerProps = {}): React.ReactElement {
  const ctx = useGroupMedia();

  // -------------------------------------------------------------------------
  // UI state — kept in refs where possible to avoid re-render cost on every
  // timeupdate (30 fps × N players). Only the values actually rendered in JSX
  // live in useState.
  // -------------------------------------------------------------------------

  // Master duration in ms — MAX across all players (shouldUpdateMasterDuration)
  const masterDurationRef = useRef<number>(0);
  const [masterDuration, setMasterDuration] = useState<number>(0);

  // Current time in ms — driven by the first non-dragging, non-playing player
  const masterPositionRef = useRef<number>(0);
  const [masterPosition, setMasterPosition] = useState<number>(0);

  // Majority-vote playing state. The ref mirrors the useState value so event
  // listeners (timeupdate) can read the CURRENT aggregate state without
  // re-subscribing on every change — a plain closure over the isPlaying
  // useState would go stale between listener attach and the next render.
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const isPlayingRef = useRef<boolean>(false);

  // Slider drag state — ref for lock-free reads inside event handlers
  const sliderDraggingRef = useRef<boolean>(false);

  // Volume: internal 0-100 integer (matches Qt slider range)
  const [volume, setVolume] = useState<number>(50);

  // Autoplay guard — fires once per grid load (not per re-render)
  const hasAutoplayed = useRef<boolean>(false);

  // -------------------------------------------------------------------------
  // Re-compute isPlaying from the current registry
  // -------------------------------------------------------------------------

  const recomputePlayingState = useCallback(() => {
    const registry = ctx.getRegistry();
    let playingCount = 0;
    registry.forEach((el) => {
      if (!el.paused) playingCount++;
    });
    const majorityPlaying = isMajorityPlaying(playingCount, registry.size);
    isPlayingRef.current = majorityPlaying;
    setIsPlaying(majorityPlaying);
  }, [ctx]);

  // -------------------------------------------------------------------------
  // Re-compute master duration as the MAX over the SURVIVING registry.
  //
  // onDurationChange (below) only ever GROWS the master (strict >), which is
  // correct while players join. But when a player DETACHES (unregister), the
  // stored master can be stale — if the longest clip left the group, the
  // slider range must shrink to the new longest survivor. This recompute runs
  // on every registry sync so a shrink is reflected; empty registry → 0.
  // -------------------------------------------------------------------------

  const recomputeMasterDuration = useCallback(() => {
    const registry = ctx.getRegistry();
    let maxMs = 0;
    registry.forEach((el) => {
      const durationMs = el.duration * 1000;
      if (isFinite(durationMs) && durationMs > maxMs) {
        maxMs = durationMs;
      }
    });
    if (maxMs !== masterDurationRef.current) {
      masterDurationRef.current = maxMs;
      setMasterDuration(maxMs);
    }
  }, [ctx]);

  // -------------------------------------------------------------------------
  // Event listener management
  //
  // We maintain a Set of paths we have already attached listeners to so we
  // don't double-attach on the React Strict Mode double-subscribe (the
  // provider's subscribe callback fires once, and in Strict Mode the effect
  // runs twice but the second cleanup removes the subscription).
  // -------------------------------------------------------------------------

  const attachedRef = useRef<Map<string, HTMLVideoElement>>(new Map());

  const attachListeners = useCallback(
    (path: string, el: HTMLVideoElement) => {
      if (attachedRef.current.get(path) === el) return; // already attached

      const onDurationChange = () => {
        const durationMs = el.duration * 1000; // HTMLVideoElement uses seconds
        if (
          isFinite(durationMs) &&
          shouldUpdateMasterDuration(durationMs, masterDurationRef.current)
        ) {
          masterDurationRef.current = durationMs;
          setMasterDuration(durationMs);
        }
      };

      const onTimeUpdate = () => {
        if (
          shouldTrackPlayerPosition(
            sliderDraggingRef.current,
            // Aggregate majority-playing state (via the ref, read live so it
            // never goes stale in this listener closure), mirroring Qt's
            // idle-only slider sync — NOT this single element's paused flag.
            isPlayingRef.current
          )
        ) {
          const posMs = el.currentTime * 1000;
          masterPositionRef.current = posMs;
          setMasterPosition(posMs);
        }
      };

      const onPlayPause = () => {
        recomputePlayingState();
      };

      el.addEventListener("durationchange", onDurationChange);
      el.addEventListener("timeupdate", onTimeUpdate);
      el.addEventListener("play", onPlayPause);
      el.addEventListener("pause", onPlayPause);

      // Store a cleanup tuple on the element via a WeakMap-style closure.
      // We stash the bound handlers so we can remove them when the element
      // is detached. Using a plain property bag approach keyed by a symbol.
      const cleanupKey = Symbol("gmc-cleanup");
      (
        el as HTMLVideoElement & {
          [key: symbol]: (() => void) | undefined;
        }
      )[cleanupKey] = () => {
        el.removeEventListener("durationchange", onDurationChange);
        el.removeEventListener("timeupdate", onTimeUpdate);
        el.removeEventListener("play", onPlayPause);
        el.removeEventListener("pause", onPlayPause);
      };

      // Tag the element so we can call cleanup later
      (el as HTMLVideoElement & { _gmcCleanupKey?: symbol })._gmcCleanupKey =
        cleanupKey;

      attachedRef.current.set(path, el);
    },
    [recomputePlayingState]
  );

  const detachListeners = useCallback(
    (path: string) => {
      const el = attachedRef.current.get(path);
      if (el === undefined) return;

      const key = (el as HTMLVideoElement & { _gmcCleanupKey?: symbol })
        ._gmcCleanupKey;
      if (key !== undefined) {
        const cleanup = (
          el as HTMLVideoElement & { [k: symbol]: (() => void) | undefined }
        )[key];
        if (typeof cleanup === "function") {
          cleanup();
        }
      }
      attachedRef.current.delete(path);
    },
    []
  );

  // -------------------------------------------------------------------------
  // Sync listener set whenever the registry changes
  // -------------------------------------------------------------------------

  const syncListeners = useCallback(() => {
    const registry = ctx.getRegistry();

    // Detach listeners for paths no longer in registry
    attachedRef.current.forEach((_, path) => {
      if (!registry.has(path)) {
        detachListeners(path);
      }
    });

    // Attach listeners for new paths
    registry.forEach((el, path) => {
      attachListeners(path, el);
    });

    // Re-sync playing state after registry change
    recomputePlayingState();

    // Reconcile master duration against the surviving registry (handles the
    // shrink case when the longest-duration player detaches).
    recomputeMasterDuration();

    // Threshold group-autoplay: fire once when all expected players have
    // registered. Guard: hasAutoplayed ensures one-shot per grid load.
    if (
      expectedPlayerCount > 0 &&
      registry.size >= expectedPlayerCount &&
      !hasAutoplayed.current
    ) {
      hasAutoplayed.current = true;
      ctx.playAll();
    }
  }, [
    ctx,
    expectedPlayerCount,
    attachListeners,
    detachListeners,
    recomputePlayingState,
    recomputeMasterDuration,
  ]);

  // -------------------------------------------------------------------------
  // Subscribe to registry changes + initial sync
  // -------------------------------------------------------------------------

  useEffect(() => {
    // Sync immediately for the current registry snapshot
    syncListeners();

    // Subscribe to future registry mutations
    const unsubscribe = ctx.subscribe(syncListeners);

    // Snapshot the ref at effect-run time so the cleanup closure does not read
    // a stale ref value (react-hooks/exhaustive-deps warning).
    const attached = attachedRef.current;

    return () => {
      unsubscribe();
      // Detach all listeners when the controller unmounts
      const paths = Array.from(attached.keys());
      paths.forEach(detachListeners);
    };
  }, [ctx, syncListeners, detachListeners]);

  // Reset autoplay guard when expectedPlayerCount changes (new grid load)
  useEffect(() => {
    hasAutoplayed.current = false;
  }, [expectedPlayerCount]);

  // -------------------------------------------------------------------------
  // Play / pause
  // -------------------------------------------------------------------------

  const handlePlayPause = useCallback(() => {
    if (isPlaying) {
      ctx.pauseAll();
    } else {
      ctx.playAll();
    }
  }, [ctx, isPlaying]);

  // -------------------------------------------------------------------------
  // Progress slider
  // -------------------------------------------------------------------------

  const handleSliderMouseDown = useCallback(() => {
    sliderDraggingRef.current = true;
  }, []);

  const handleSliderChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      // During drag, update current-time label without seeking
      const valueMs = Number(e.target.value);
      setMasterPosition(valueMs);
      masterPositionRef.current = valueMs;
    },
    []
  );

  // Seek-on-RELEASE: shared by the pointer-up path and the keyboard path so
  // both commit the seek only when the interaction ends (faithful to the Qt
  // slider, which emits sliderReleased). Deliberately NOT wired to onChange —
  // React onChange on a range input fires continuously during a drag, which
  // would seek-spam every registered player and fight playback.
  const seekToSliderValue = useCallback(
    (valueMs: number) => {
      sliderDraggingRef.current = false;
      const position = computeMasterPosition(
        masterDurationRef.current > 0 ? valueMs / masterDurationRef.current : 0,
        masterDurationRef.current
      );
      if (position !== null) {
        ctx.seekAll(position);
      }
    },
    [ctx]
  );

  const handleSliderMouseUp = useCallback(
    (e: React.MouseEvent<HTMLInputElement>) => {
      seekToSliderValue(Number((e.target as HTMLInputElement).value));
    },
    [seekToSliderValue]
  );

  // Keyboard scrub (arrow keys / Home / End move a focused range input): seek
  // on key-release so keyboard users get the same release-commit semantics as
  // a pointer drag, and the control is operable without a mouse (a11y).
  const handleSliderKeyUp = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      seekToSliderValue(Number((e.target as HTMLInputElement).value));
    },
    [seekToSliderValue]
  );

  // -------------------------------------------------------------------------
  // Volume
  // -------------------------------------------------------------------------

  const handleVolumeChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = Number(e.target.value);
      setVolume(v);
      ctx.setVolumeAll(v / 100);
    },
    [ctx]
  );

  const handleMute = useCallback(() => {
    // computeMuteTargetVolume operates on 0..1 scale; convert to/from 0..100
    const currentV01 = volume / 100;
    const targetV01 = computeMuteTargetVolume(currentV01, DEFAULT_UNMUTE_VOLUME);
    const targetV100 = Math.round(targetV01 * 100);
    setVolume(targetV100);
    ctx.setVolumeAll(targetV01);
  }, [ctx, volume]);

  // -------------------------------------------------------------------------
  // Derived display values
  // -------------------------------------------------------------------------

  const playIcon = playButtonIconForState(isPlaying);
  const muteIcon = volumeIconForValue(volume / 100);
  const currentTimeLabel = formatDuration(masterPosition);
  const durationLabel = formatDuration(masterDuration);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div
      data-testid={GMC_BAR}
      className="flex items-center gap-2 px-3 py-2 bg-neutral-900 text-white text-sm select-none"
    >
      {/* Play / Pause */}
      <button
        data-testid={GMC_PLAY_PAUSE}
        onClick={handlePlayPause}
        className="w-9 h-8 flex items-center justify-center rounded bg-neutral-700 hover:bg-neutral-600 active:bg-neutral-500 flex-shrink-0"
        aria-label={isPlaying ? "Pause all" : "Play all"}
        type="button"
      >
        {playIcon}
      </button>

      {/* Current time */}
      <span
        data-testid={GMC_CURRENT_TIME}
        className="font-mono text-xs text-neutral-300 w-12 text-right flex-shrink-0"
      >
        {currentTimeLabel}
      </span>

      {/* Progress slider */}
      <input
        data-testid={GMC_PROGRESS_SLIDER}
        type="range"
        min={0}
        max={masterDuration > 0 ? masterDuration : 0}
        value={masterPosition}
        onMouseDown={handleSliderMouseDown}
        onChange={handleSliderChange}
        onMouseUp={handleSliderMouseUp}
        onKeyUp={handleSliderKeyUp}
        className="flex-1 accent-white cursor-pointer min-w-0"
        aria-label="Playback position"
      />

      {/* Duration */}
      <span
        data-testid={GMC_DURATION}
        className="font-mono text-xs text-neutral-300 w-12 flex-shrink-0"
      >
        {durationLabel}
      </span>

      {/* Mute button */}
      <button
        data-testid={GMC_MUTE_BUTTON}
        onClick={handleMute}
        className="w-8 h-8 flex items-center justify-center rounded bg-neutral-700 hover:bg-neutral-600 active:bg-neutral-500 flex-shrink-0"
        aria-label={volume === 0 ? "Unmute" : "Mute"}
        type="button"
      >
        {muteIcon}
      </button>

      {/* Volume slider */}
      <input
        data-testid={GMC_VOLUME_SLIDER}
        type="range"
        min={0}
        max={100}
        value={volume}
        onChange={handleVolumeChange}
        className="w-28 accent-white cursor-pointer flex-shrink-0"
        aria-label="Volume"
      />
    </div>
  );
}
