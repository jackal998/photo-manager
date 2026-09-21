// GroupMedia React context object — in its own file so ESLint's
// react-refresh/only-export-components rule is satisfied. The context
// is shared between GroupMediaProvider.tsx (which wraps children) and
// hooks/useGroupMedia.ts (which consumes it).

import { createContext } from "react";

// ---------------------------------------------------------------------------
// Public API surface
// ---------------------------------------------------------------------------

export interface GroupMediaContextValue {
  /**
   * Register an HTMLVideoElement for the given file path. Called by each tile
   * on mount (or when its <video> element is first created). Overwrites any
   * previous registration for the same path (React Strict Mode safe).
   */
  register(path: string, el: HTMLVideoElement): void;

  /**
   * Remove the registration for the given file path. Called by each tile on
   * unmount. No-op if the path is not registered.
   */
  unregister(path: string): void;

  /**
   * Return a snapshot of the current registry (path → element). The returned
   * Map is a live view of the internal map — do not mutate it.
   */
  getRegistry(): ReadonlyMap<string, HTMLVideoElement>;

  /**
   * Subscribe to registry changes (register / unregister). Returns an
   * unsubscribe function. Callers (controller UI) use this to re-render
   * when the registry size changes.
   */
  subscribe(callback: () => void): () => void;

  // -------------------------------------------------------------------------
  // Imperative broadcast methods
  // -------------------------------------------------------------------------

  /** Call .play() on every registered element. */
  playAll(): void;

  /** Call .pause() on every registered element. */
  pauseAll(): void;

  /**
   * Seek every registered element to ``ms`` milliseconds.
   * Sets el.currentTime = ms / 1000 (HTMLVideoElement uses seconds).
   */
  seekAll(ms: number): void;

  /**
   * Set volume on every registered element. ``v`` is in the 0..1 range
   * (HTMLVideoElement.volume scale). Values are clamped to [0, 1].
   */
  setVolumeAll(v: number): void;
}

export const GroupMediaContext = createContext<GroupMediaContextValue | null>(null);
