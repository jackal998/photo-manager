// Unit cover for useBeforeUnloadGuard — the conditional close-guard.
//
// Non-vacuity is the whole point: an always-on handler would also pass an
// "armed while running" assertion, so we assert BOTH that it arms while
// running AND that it is dormant for every non-running status. The event is
// dispatched through window.dispatchEvent and we read defaultPrevented on that
// same event — the test never calls preventDefault itself (that would be a
// tautology); only the hook under test may.

import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

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
