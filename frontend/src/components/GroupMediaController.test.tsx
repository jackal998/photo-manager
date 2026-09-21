// Component tests for GroupMediaController — the synchronized-playback control
// bar for a grid of video tiles.
//
// These tests drive the REAL coordination surface (registry sync, master
// duration reconciliation, the one-shot autoplay guard, listener cleanup) via
// fake <video> stubs registered through the real GroupMediaProvider. Each case
// pins a documented real failure mode (out-of-order durationchange shrinking
// the master; the autoplay guard firing more than once; leaked listeners on
// unregister/unmount) — no "branch was reached" assertions, per CLAUDE.md.

import { act, render } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { useEffect } from "react";

import { GroupMediaController } from "./GroupMediaController";
import { GroupMediaProvider } from "./GroupMediaProvider";
import { useGroupMedia } from "@/hooks/useGroupMedia";

// ---------------------------------------------------------------------------
// Fake <video> stub — the minimal HTMLVideoElement surface the controller
// touches: duration/currentTime/paused/volume properties, play/pause methods,
// and add/removeEventListener with a dispatch() helper to fire media events.
// ---------------------------------------------------------------------------

interface FakeVideo {
  el: HTMLVideoElement;
  dispatch: (type: string) => void;
  listenerCount: () => number;
  play: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  setDuration: (seconds: number) => void;
  setPaused: (paused: boolean) => void;
}

function makeFakeVideo(initialDurationS: number): FakeVideo {
  const listeners = new Map<string, Set<() => void>>();

  const play = vi.fn(() => {
    obj.paused = false;
    return Promise.resolve();
  });
  const pause = vi.fn(() => {
    obj.paused = true;
  });

  const obj = {
    duration: initialDurationS,
    currentTime: 0,
    paused: true,
    volume: 0.5,
    play,
    pause,
    addEventListener(type: string, cb: () => void) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(cb);
    },
    removeEventListener(type: string, cb: () => void) {
      listeners.get(type)?.delete(cb);
    },
  };

  const dispatch = (type: string) => {
    listeners.get(type)?.forEach((cb) => cb());
  };

  const listenerCount = () => {
    let n = 0;
    listeners.forEach((set) => (n += set.size));
    return n;
  };

  return {
    el: obj as unknown as HTMLVideoElement,
    dispatch,
    listenerCount,
    play,
    pause,
    setDuration: (seconds: number) => {
      obj.duration = seconds;
    },
    setPaused: (paused: boolean) => {
      obj.paused = paused;
    },
  };
}

// ---------------------------------------------------------------------------
// Harness — a child component that registers/unregisters fake videos on demand
// so the test can mutate the registry the same way real VideoTiles do.
// ---------------------------------------------------------------------------

type RegistryOp = { path: string; el: HTMLVideoElement };

function Registrar({ ops }: { ops: RegistryOp[] }) {
  const { register, unregister } = useGroupMedia();
  useEffect(() => {
    ops.forEach(({ path, el }) => register(path, el));
    return () => {
      ops.forEach(({ path }) => unregister(path));
    };
    // Register exactly the ops passed on mount; the test re-renders with a new
    // array reference when it wants to change the registry.
  }, [ops, register, unregister]);
  return null;
}

// ---------------------------------------------------------------------------
// Master duration — out-of-order durationchange must keep the MAX
// ---------------------------------------------------------------------------

describe("GroupMediaController master duration", () => {
  it("keeps the slider max at the LONGEST duration when a shorter player reports last", () => {
    const long = makeFakeVideo(0); // duration filled in via durationchange
    const short = makeFakeVideo(0);

    const ops: RegistryOp[] = [
      { path: "/a/long.mp4", el: long.el },
      { path: "/a/short.mp4", el: short.el },
    ];

    const { container } = render(
      <GroupMediaProvider>
        <Registrar ops={ops} />
        <GroupMediaController expectedPlayerCount={2} />
      </GroupMediaProvider>
    );

    const slider = container.querySelector<HTMLInputElement>(
      '[data-testid="gmc-progress-slider"]'
    )!;

    // Long player reports 10s FIRST, then the short player reports 3s LAST.
    act(() => {
      long.setDuration(10);
      long.dispatch("durationchange");
    });
    act(() => {
      short.setDuration(3);
      short.dispatch("durationchange");
    });

    // Failure mode: a `!=`/last-wins duration handler would shrink the master
    // to 3000 when the short player's event arrives last. The MAX must hold.
    expect(slider.max).toBe("10000");
  });

  it("shrinks the master to the surviving MAX when the longest player detaches", () => {
    const long = makeFakeVideo(10);
    const short = makeFakeVideo(3);

    const bothOps: RegistryOp[] = [
      { path: "/a/long.mp4", el: long.el },
      { path: "/a/short.mp4", el: short.el },
    ];

    const { container, rerender } = render(
      <GroupMediaProvider>
        <Registrar ops={bothOps} />
        <GroupMediaController expectedPlayerCount={2} />
      </GroupMediaProvider>
    );

    const slider = container.querySelector<HTMLInputElement>(
      '[data-testid="gmc-progress-slider"]'
    )!;

    act(() => {
      long.dispatch("durationchange");
      short.dispatch("durationchange");
    });
    expect(slider.max).toBe("10000");

    // The long player detaches (its Registrar unmounts by swapping ops to the
    // short-only array). The master must shrink to the survivor's 3000.
    const shortOnly: RegistryOp[] = [{ path: "/a/short.mp4", el: short.el }];
    act(() => {
      rerender(
        <GroupMediaProvider>
          <Registrar ops={shortOnly} />
          <GroupMediaController expectedPlayerCount={2} />
        </GroupMediaProvider>
      );
    });

    // Failure mode: a grow-only master (strict > on register) would leave the
    // slider stuck at 10000 after the 10s clip left the group.
    expect(slider.max).toBe("3000");
  });
});

// ---------------------------------------------------------------------------
// Autoplay guard — fires playAll EXACTLY ONCE at expectedPlayerCount
// ---------------------------------------------------------------------------

describe("GroupMediaController autoplay guard", () => {
  it("calls playAll exactly once when the registry reaches expectedPlayerCount, and not again on churn", () => {
    const a = makeFakeVideo(5);
    const b = makeFakeVideo(5);

    // Start with only one player registered → threshold (2) not yet reached.
    const oneOp: RegistryOp[] = [{ path: "/a/a.mp4", el: a.el }];

    const { rerender } = render(
      <GroupMediaProvider>
        <Registrar ops={oneOp} />
        <GroupMediaController expectedPlayerCount={2} />
      </GroupMediaProvider>
    );

    // One player registered — below threshold, no autoplay yet.
    expect(a.play).not.toHaveBeenCalled();

    // Second player registers → registry size hits expectedPlayerCount (2) →
    // playAll() fires once, calling .play() on BOTH registered elements.
    const twoOps: RegistryOp[] = [
      { path: "/a/a.mp4", el: a.el },
      { path: "/a/b.mp4", el: b.el },
    ];
    act(() => {
      rerender(
        <GroupMediaProvider>
          <Registrar ops={twoOps} />
          <GroupMediaController expectedPlayerCount={2} />
        </GroupMediaProvider>
      );
    });

    expect(a.play).toHaveBeenCalledTimes(1);
    expect(b.play).toHaveBeenCalledTimes(1);

    // Further registry churn (a play/pause event that re-syncs, and a
    // no-op re-render) must NOT re-fire the one-shot autoplay.
    act(() => {
      a.setPaused(false);
      a.dispatch("play");
      b.dispatch("pause");
    });
    act(() => {
      rerender(
        <GroupMediaProvider>
          <Registrar ops={twoOps} />
          <GroupMediaController expectedPlayerCount={2} />
        </GroupMediaProvider>
      );
    });

    // Failure mode: a guard keyed on render (not a one-shot ref) would call
    // playAll again on every sync, restarting playback under the user.
    expect(a.play).toHaveBeenCalledTimes(1);
    expect(b.play).toHaveBeenCalledTimes(1);
  });

  it("does not autoplay when expectedPlayerCount is 0 (autoplay disabled)", () => {
    const a = makeFakeVideo(5);
    const ops: RegistryOp[] = [{ path: "/a/a.mp4", el: a.el }];

    render(
      <GroupMediaProvider>
        <Registrar ops={ops} />
        <GroupMediaController expectedPlayerCount={0} />
      </GroupMediaProvider>
    );

    expect(a.play).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Listener cleanup — detach on unregister and on controller unmount
// ---------------------------------------------------------------------------

describe("GroupMediaController listener cleanup", () => {
  it("removes all listeners from a player when it unregisters", () => {
    const a = makeFakeVideo(5);
    const b = makeFakeVideo(5);

    const bothOps: RegistryOp[] = [
      { path: "/a/a.mp4", el: a.el },
      { path: "/a/b.mp4", el: b.el },
    ];

    const { rerender } = render(
      <GroupMediaProvider>
        <Registrar ops={bothOps} />
        <GroupMediaController expectedPlayerCount={2} />
      </GroupMediaProvider>
    );

    // The controller attaches durationchange/timeupdate/play/pause (4 listeners).
    expect(a.listenerCount()).toBe(4);
    expect(b.listenerCount()).toBe(4);

    // Detach player A by swapping to a B-only registry.
    const bOnly: RegistryOp[] = [{ path: "/a/b.mp4", el: b.el }];
    act(() => {
      rerender(
        <GroupMediaProvider>
          <Registrar ops={bOnly} />
          <GroupMediaController expectedPlayerCount={2} />
        </GroupMediaProvider>
      );
    });

    // Failure mode: a detach that forgets to call the stored cleanup would
    // leave A's 4 listeners live, so a later durationchange on the removed
    // element would still mutate the master (a use-after-detach bug).
    expect(a.listenerCount()).toBe(0);
    expect(b.listenerCount()).toBe(4);
  });

  it("removes all listeners from every player when the controller unmounts", () => {
    const a = makeFakeVideo(5);
    const b = makeFakeVideo(5);

    const bothOps: RegistryOp[] = [
      { path: "/a/a.mp4", el: a.el },
      { path: "/a/b.mp4", el: b.el },
    ];

    const { unmount } = render(
      <GroupMediaProvider>
        <Registrar ops={bothOps} />
        <GroupMediaController expectedPlayerCount={2} />
      </GroupMediaProvider>
    );

    expect(a.listenerCount()).toBe(4);
    expect(b.listenerCount()).toBe(4);

    act(() => {
      unmount();
    });

    // Failure mode: an unmount that skips detachListeners leaks listeners onto
    // detached DOM nodes — the classic "controller gone but still reacting to
    // media events" bug.
    expect(a.listenerCount()).toBe(0);
    expect(b.listenerCount()).toBe(0);
  });
});
