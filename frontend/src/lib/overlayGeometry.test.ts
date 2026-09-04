// Unit cover for the persisted overlay-geometry store (#739).
//
// Every case here is a way a user loses access to a dialog, not a branch count:
// a geometry saved near the edge of a big monitor and reopened in a small
// window (title bar off-screen => the window can never be dragged back), a
// saved size bigger than the viewport (footer buttons unreachable), a corrupt
// blob, and the three surfaces sharing storage (moving one would move the
// others). The clamp is the only thing standing between those and a stuck UI.

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clampGeometry,
  loadOverlayGeometry,
  overlayStorageKey,
  saveOverlayGeometry,
  MIN_OVERLAY_HEIGHT,
  MIN_OVERLAY_WIDTH,
} from "./overlayGeometry";

const VIEWPORT = { width: 1280, height: 800 };

beforeEach(() => {
  localStorage.clear();
});

describe("overlayStorageKey", () => {
  it("gives each surface its own key, distinct from the other persisted layout keys", () => {
    const keys = [
      overlayStorageKey("fullres"),
      overlayStorageKey("execute"),
      overlayStorageKey("action"),
    ];
    expect(new Set(keys).size).toBe(3);
    // #739 preview splitter and #685 column widths must not be shadowed.
    expect(keys).not.toContain("panelWidths");
    expect(keys).not.toContain("pm.result-tree.column-widths.v1");
  });
});

describe("clampGeometry", () => {
  it("leaves a geometry that already fits untouched", () => {
    expect(clampGeometry({ x: 100, y: 60, w: 800, h: 600 }, VIEWPORT)).toEqual({
      x: 100,
      y: 60,
      w: 800,
      h: 600,
    });
  });

  it("pulls a window saved past the right/bottom edge fully back into view", () => {
    // Saved on a 2560x1440 monitor, reopened at 1280x800.
    const clamped = clampGeometry({ x: 2200, y: 1300, w: 600, h: 400 }, VIEWPORT);
    expect(clamped.x + clamped.w).toBeLessThanOrEqual(VIEWPORT.width);
    expect(clamped.y + clamped.h).toBeLessThanOrEqual(VIEWPORT.height);
    expect(clamped).toEqual({ x: 680, y: 400, w: 600, h: 400 });
  });

  it("pulls a window saved at negative coordinates back to the origin", () => {
    // The title bar is the ONLY drag affordance; above y=0 it is unreachable.
    expect(clampGeometry({ x: -500, y: -120, w: 600, h: 400 }, VIEWPORT)).toEqual({
      x: 0,
      y: 0,
      w: 600,
      h: 400,
    });
  });

  it("shrinks a saved size larger than the viewport", () => {
    expect(clampGeometry({ x: 0, y: 0, w: 4000, h: 3000 }, VIEWPORT)).toEqual({
      x: 0,
      y: 0,
      w: VIEWPORT.width,
      h: VIEWPORT.height,
    });
  });

  it("enforces the minimum size floor", () => {
    const clamped = clampGeometry({ x: 10, y: 10, w: 5, h: 5 }, VIEWPORT);
    expect(clamped.w).toBe(MIN_OVERLAY_WIDTH);
    expect(clamped.h).toBe(MIN_OVERLAY_HEIGHT);
  });

  it("keeps the minimum size even when the viewport is smaller than it", () => {
    // A phone-sized viewport must still produce a usable (if overflowing)
    // window rather than a zero-width one.
    const clamped = clampGeometry(
      { x: 40, y: 40, w: 600, h: 400 },
      { width: 200, height: 150 }
    );
    expect(clamped.w).toBe(MIN_OVERLAY_WIDTH);
    expect(clamped.h).toBe(MIN_OVERLAY_HEIGHT);
    expect(clamped.x).toBe(0);
    expect(clamped.y).toBe(0);
  });
});

describe("save/load round-trip", () => {
  it("restores what was saved", () => {
    saveOverlayGeometry("execute", { x: 120, y: 90, w: 700, h: 500 });
    expect(loadOverlayGeometry("execute", VIEWPORT)).toEqual({
      x: 120,
      y: 90,
      w: 700,
      h: 500,
    });
  });

  it("keeps the three surfaces independent", () => {
    saveOverlayGeometry("execute", { x: 10, y: 10, w: 700, h: 500 });
    saveOverlayGeometry("action", { x: 300, y: 200, w: 500, h: 400 });
    expect(loadOverlayGeometry("execute", VIEWPORT)).toMatchObject({ x: 10 });
    expect(loadOverlayGeometry("action", VIEWPORT)).toMatchObject({ x: 300 });
    // The viewer was never moved — it must still use its own default layout.
    expect(loadOverlayGeometry("fullres", VIEWPORT)).toBeNull();
  });

  it("clamps on LOAD, so a geometry saved on a bigger screen still opens in view", () => {
    saveOverlayGeometry("fullres", { x: 2000, y: 1200, w: 900, h: 700 });
    const restored = loadOverlayGeometry("fullres", VIEWPORT);
    expect(restored).not.toBeNull();
    expect(restored!.x + restored!.w).toBeLessThanOrEqual(VIEWPORT.width);
    expect(restored!.y + restored!.h).toBeLessThanOrEqual(VIEWPORT.height);
  });

  it("returns null (=> surface default) for a missing key", () => {
    expect(loadOverlayGeometry("action", VIEWPORT)).toBeNull();
  });

  it.each([
    ["not json at all", "{{{"],
    ["a non-object", "42"],
    ["a partial rect", '{"x":10,"y":10,"w":600}'],
    ["a non-numeric field", '{"x":"10","y":10,"w":600,"h":400}'],
    ["NaN-producing nulls", '{"x":null,"y":null,"w":null,"h":null}'],
    ["a zero-sized rect", '{"x":10,"y":10,"w":0,"h":0}'],
  ])("discards %s rather than rendering an unusable window", (_label, raw) => {
    localStorage.setItem(overlayStorageKey("execute"), raw);
    expect(loadOverlayGeometry("execute", VIEWPORT)).toBeNull();
  });

  it("survives a storage backend that throws (private mode / quota)", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });
    // Must not propagate: geometry is a convenience, never a correctness
    // invariant — a throwing save may not take the dialog down with it.
    expect(() =>
      saveOverlayGeometry("action", { x: 1, y: 1, w: 600, h: 400 })
    ).not.toThrow();
    spy.mockRestore();

    const getSpy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new DOMException("SecurityError");
      });
    expect(loadOverlayGeometry("action", VIEWPORT)).toBeNull();
    getSpy.mockRestore();
  });
});
