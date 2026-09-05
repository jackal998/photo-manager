// GroupMediaProvider — React context that owns the Map<filePath, HTMLVideoElement>
// registry and exposes imperative broadcast methods (playAll / pauseAll / seekAll /
// setVolumeAll).
//
// Design choices:
//
// 1. DOM element refs live here (NOT in Zustand). The Zustand store is for
//    serialisable state (group selection, manifest). HTMLVideoElement refs are
//    imperative handles that can't be serialised and must not trigger global
//    re-renders on every frame of video playback.
//
// 2. Subscribers (controller UI) get re-rendered only on registry changes
//    (register / unregister), not on playback events. The subscription is a
//    plain Set<() => void> of callbacks rather than a Zustand slice to keep
//    DOM-element churn out of the reactive graph.
//
// 3. playAll / pauseAll / seekAll / setVolumeAll are imperative — they call
//    el.play() / el.pause() / el.currentTime / el.volume directly and return
//    void. Callers (controller bar) do not await them; individual tile error
//    handling is per-element (transcode-fallback pattern).
//
// 4. Each tile component must call register(path, el) on mount and
//    unregister(path) on unmount. The provider never stores a stale ref —
//    unregister removes the entry, register overwrites it if a tile remounts
//    with the same path (which happens on React Strict Mode double-invoke).
//
// Context object and interface live in GroupMediaContext.ts; the consumer hook
// lives in hooks/useGroupMedia.ts — both split out to satisfy ESLint's
// react-refresh/only-export-components rule.

import React, { useCallback, useRef } from "react";
import {
  GroupMediaContext,
  type GroupMediaContextValue,
} from "./GroupMediaContext";

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

interface GroupMediaProviderProps {
  children: React.ReactNode;
}

export function GroupMediaProvider({
  children,
}: GroupMediaProviderProps): React.ReactElement {
  // The live registry — Map is mutated in place (never replaced) so broadcast
  // callbacks always iterate the current snapshot without needing a re-render.
  const registryRef = useRef<Map<string, HTMLVideoElement>>(new Map());

  // Subscriber set — controller UI components call subscribe() so they re-render
  // when tiles mount/unmount. Kept in a ref so subscribe/unsubscribe are stable.
  const subscribersRef = useRef<Set<() => void>>(new Set());

  const notify = useCallback(() => {
    subscribersRef.current.forEach((cb) => cb());
  }, []);

  const register = useCallback(
    (path: string, el: HTMLVideoElement) => {
      registryRef.current.set(path, el);
      notify();
    },
    [notify]
  );

  const unregister = useCallback(
    (path: string) => {
      const deleted = registryRef.current.delete(path);
      if (deleted) notify();
    },
    [notify]
  );

  const getRegistry = useCallback(
    (): ReadonlyMap<string, HTMLVideoElement> => registryRef.current,
    []
  );

  const subscribe = useCallback((callback: () => void) => {
    subscribersRef.current.add(callback);
    return () => {
      subscribersRef.current.delete(callback);
    };
  }, []);

  // -------------------------------------------------------------------------
  // Broadcast methods — imperative, no state updates
  // -------------------------------------------------------------------------

  const playAll = useCallback(() => {
    registryRef.current.forEach((el) => {
      // .play() returns a Promise; rejections (e.g. AbortError on rapid
      // play/pause) are intentionally swallowed here — each tile's own
      // onError handler manages transcode-fallback state.
      void el.play().catch(() => undefined);
    });
  }, []);

  const pauseAll = useCallback(() => {
    registryRef.current.forEach((el) => {
      el.pause();
    });
  }, []);

  const seekAll = useCallback((ms: number) => {
    const seconds = ms / 1000;
    registryRef.current.forEach((el) => {
      el.currentTime = seconds;
    });
  }, []);

  const setVolumeAll = useCallback((v: number) => {
    const clamped = Math.min(1, Math.max(0, v));
    registryRef.current.forEach((el) => {
      el.volume = clamped;
    });
  }, []);

  // Stable value object — all methods are memoised with useCallback so the
  // context value reference is stable across re-renders of the provider.
  const value: GroupMediaContextValue = {
    register,
    unregister,
    getRegistry,
    subscribe,
    playAll,
    pauseAll,
    seekAll,
    setVolumeAll,
  };

  return (
    <GroupMediaContext.Provider value={value}>
      {children}
    </GroupMediaContext.Provider>
  );
}
