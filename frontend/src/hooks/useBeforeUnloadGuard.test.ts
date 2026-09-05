// Unit cover for useBeforeUnloadGuard — the conditional close-guard.
//
// Non-vacuity is the whole point: an always-on handler would also pass an
// "armed while running" assertion, so we assert BOTH that it arms while
// running AND that it is dormant for every non-running status. The event is
// dispatched through window.dispatchEvent and we read defaultPrevented on that
// same event — the test never calls preventDefault itself (that would be a
// tautology); only the hook under test may.

import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useBeforeUnloadGuard } from "./useBeforeUnloadGuard";
import { useAppStore } from "../store/useAppStore";

function setStatus(status: string): void {
  const prev = useAppStore.getState().scan;
  useAppStore.setState({ scan: { ...prev, status: status as never } });
}

function dispatchBeforeUnload(): boolean {
  const e = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(e);
  return e.defaultPrevented;
}

afterEach(() => {
  setStatus("idle");
});

describe("useBeforeUnloadGuard", () => {
  it("arms (blocks unload) while a scan is running", () => {
    renderHook(() => useBeforeUnloadGuard());
    setStatus("running");
    expect(dispatchBeforeUnload()).toBe(true);
  });

  it.each(["idle", "finished", "failed", "cancelled"])(
    "is dormant when status is %s (does not block unload)",
    (status) => {
      renderHook(() => useBeforeUnloadGuard());
      setStatus(status);
      expect(dispatchBeforeUnload()).toBe(false);
    }
  );

  it("releases the block when the scan transitions running → cancelled", () => {
    renderHook(() => useBeforeUnloadGuard());
    setStatus("running");
    expect(dispatchBeforeUnload()).toBe(true);
    setStatus("cancelled");
    expect(dispatchBeforeUnload()).toBe(false);
  });

  it("removes its listener on unmount (no leaked guard)", () => {
    const { unmount } = renderHook(() => useBeforeUnloadGuard());
    setStatus("running");
    unmount();
    // After unmount the listener is gone, so even a running scan no longer
    // blocks — proves the cleanup deregistered the handler.
    expect(dispatchBeforeUnload()).toBe(false);
  });
});

// #703: pagehide → best-effort cancel-on-leave. jsdom has no navigator.sendBeacon,
// so the spy is INSTALLED (not spyOn'd — spyOn throws on a missing property)
// before the hook runs. Non-vacuity: the positive case sets BOTH running + a
// concrete taskId and asserts the exact URL; negatives cover every non-running
// status, a null taskId, a bfcache stash (persisted=true), and post-unmount — so a
// wired-always or wired-never listener fails.

function setRunning(taskId: string | null): void {
  const prev = useAppStore.getState().scan;
  useAppStore.setState({ scan: { ...prev, status: "running" as never, taskId } });
}

function dispatchPagehide(persisted = false): void {
  // jsdom can't construct PageTransitionEvent; set `persisted` as an own prop the
  // handler reads off a plain pagehide Event.
  const e = new Event("pagehide");
  Object.defineProperty(e, "persisted", { value: persisted, configurable: true });
  window.dispatchEvent(e);
}

describe("useBeforeUnloadGuard — pagehide cancel-on-leave (#703)", () => {
  let beaconSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    beaconSpy = vi.fn();
    Object.defineProperty(navigator, "sendBeacon", {
      value: beaconSpy,
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    delete (navigator as { sendBeacon?: unknown }).sendBeacon;
    setStatus("idle");
  });

  it("registers a 'pagehide' listener", () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    renderHook(() => useBeforeUnloadGuard());
    expect(addSpy).toHaveBeenCalledWith("pagehide", expect.any(Function));
    addSpy.mockRestore();
  });

  it("beacons the cancel endpoint when a scan is running (exact URL)", () => {
    renderHook(() => useBeforeUnloadGuard());
    setRunning("task-abc");
    dispatchPagehide();
    expect(beaconSpy).toHaveBeenCalledTimes(1);
    expect(beaconSpy.mock.calls[0][0]).toBe("/api/scan/task-abc/cancel");
  });

  it.each(["idle", "finished", "failed", "cancelled"])(
    "does NOT beacon when status is %s (even with a taskId set)",
    (status) => {
      renderHook(() => useBeforeUnloadGuard());
      const prev = useAppStore.getState().scan;
      useAppStore.setState({
        scan: { ...prev, status: status as never, taskId: "task-abc" },
      });
      dispatchPagehide();
      expect(beaconSpy).not.toHaveBeenCalled();
    }
  );

  it("does NOT beacon when running but taskId is null", () => {
    renderHook(() => useBeforeUnloadGuard());
    setRunning(null);
    dispatchPagehide();
    expect(beaconSpy).not.toHaveBeenCalled();
  });

  it("does NOT beacon on a bfcache stash (pagehide persisted=true)", () => {
    renderHook(() => useBeforeUnloadGuard());
    setRunning("task-abc");
    dispatchPagehide(true);
    expect(beaconSpy).not.toHaveBeenCalled();
  });

  it("removes its pagehide listener on unmount (no late beacon)", () => {
    const { unmount } = renderHook(() => useBeforeUnloadGuard());
    setRunning("task-abc");
    unmount();
    dispatchPagehide();
    expect(beaconSpy).not.toHaveBeenCalled();
  });
});
