// Unit cover for the persisted overlay-geometry store (#739).
//
// Every case here is a way a user loses access to a dialog, not a branch count:
// a geometry saved near the edge of a big monitor and reopened in a small
// window (title bar off-screen => the window can never be dragged back), a
// saved size bigger than the viewport (footer buttons unreachable), a corrupt
// blob, and the three surfaces sharing storage (moving one would move the
// others). The clamp is the only thing standing between those and a stuck UI.
//
// Round 2 added the size-is-optional rule: `w`/`h` are null until the user
// actually RESIZES, so moving a dialog can never freeze its height. The tests
// below pin both halves — a null size survives a round-trip, and the position
// clamp still works using the element's measured size in its place.

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
/** Stands in for the rendered box of an overlay with no pinned size. */
const MEASURED = { w: 600, h: 400 };

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

describe("clampGeometry — pinned size", () => {
  it("leaves a geometry that already fits untouched", () => {
    expect(
      clampGeometry({ x: 100, y: 60, w: 800, h: 600 }, VIEWPORT, MEASURED)
    ).toEqual({ x: 100, y: 60, w: 800, h: 600 });
  });

  it("pulls a window saved past the right/bottom edge fully back into view", () => {
    // Saved on a 2560x1440 monitor, reopened at 1280x800.
    const clamped = clampGeometry(
      { x: 2200, y: 1300, w: 600, h: 400 },
      VIEWPORT,
      MEASURED
    );
    expect(clamped.x + clamped.w!).toBeLessThanOrEqual(VIEWPORT.width);
    expect(clamped.y + clamped.h!).toBeLessThanOrEqual(VIEWPORT.height);
    expect(clamped).toEqual({ x: 680, y: 400, w: 600, h: 400 });
  });

  it("pulls a window saved at negative coordinates back to the origin", () => {
    // The title bar is the ONLY drag affordance; above y=0 it is unreachable.
    expect(
      clampGeometry({ x: -500, y: -120, w: 600, h: 400 }, VIEWPORT, MEASURED)
    ).toEqual({ x: 0, y: 0, w: 600, h: 400 });
  });

  it("shrinks a saved size larger than the viewport", () => {
    expect(
      clampGeometry({ x: 0, y: 0, w: 4000, h: 3000 }, VIEWPORT, MEASURED)
    ).toEqual({ x: 0, y: 0, w: VIEWPORT.width, h: VIEWPORT.height });
  });

  it("enforces the minimum size floor", () => {
    const clamped = clampGeometry({ x: 10, y: 10, w: 5, h: 5 }, VIEWPORT, MEASURED);
    expect(clamped.w).toBe(MIN_OVERLAY_WIDTH);
    expect(clamped.h).toBe(MIN_OVERLAY_HEIGHT);
  });

  it("keeps the minimum size even when the viewport is smaller than it", () => {
    // A phone-sized viewport must still produce a usable (if overflowing)
    // window rather than a zero-width one.
    const clamped = clampGeometry(
      { x: 40, y: 40, w: 600, h: 400 },
      { width: 200, height: 150 },
      MEASURED
    );
    expect(clamped.w).toBe(MIN_OVERLAY_WIDTH);
    expect(clamped.h).toBe(MIN_OVERLAY_HEIGHT);
    expect(clamped.x).toBe(0);
    expect(clamped.y).toBe(0);
  });
});

describe("clampGeometry — unpinned (position-only) size", () => {
  it("never invents a size for an overlay the user only moved", () => {
    const clamped = clampGeometry({ x: 100, y: 60, w: null, h: null }, VIEWPORT, MEASURED);
    // Pinning here is the round-2 HIGH: a dialog whose height was frozen by a
    // drag cannot grow when its preview appears, and the footer leaves the box.
    expect(clamped.w).toBeNull();
    expect(clamped.h).toBeNull();
  });

  it("clamps the position using the MEASURED size in place of a stored one", () => {
    // x=1200 with a 600px-wide rendered box would hang 520px off the right.
    const clamped = clampGeometry(
      { x: 1200, y: 700, w: null, h: null },
      VIEWPORT,
      MEASURED
    );
    expect(clamped.x).toBe(VIEWPORT.width - MEASURED.w); // 680
    expect(clamped.y).toBe(VIEWPORT.height - MEASURED.h); // 400
  });

  it("clamps a half-pinned geometry against the measured size on the free axis", () => {
    const clamped = clampGeometry(
      { x: 1200, y: 700, w: 900, h: null },
      VIEWPORT,
      MEASURED
    );
    expect(clamped.x).toBe(VIEWPORT.width - 900);
    expect(clamped.y).toBe(VIEWPORT.height - MEASURED.h);
    expect(clamped.h).toBeNull();
  });
});

describe("save/load round-trip", () => {
  it("restores what was saved", () => {
    saveOverlayGeometry("execute", { x: 120, y: 90, w: 700, h: 500 });
    expect(loadOverlayGeometry("execute")).toEqual({
      x: 120,
      y: 90,
      w: 700,
      h: 500,
    });
  });

  it("round-trips a position-only geometry without inventing a size", () => {
    saveOverlayGeometry("action", { x: 120, y: 90, w: null, h: null });
    expect(loadOverlayGeometry("action")).toEqual({
      x: 120,
      y: 90,
      w: null,
      h: null,
    });
  });

  it("keeps the three surfaces independent", () => {
    saveOverlayGeometry("execute", { x: 10, y: 10, w: 700, h: 500 });
    saveOverlayGeometry("action", { x: 300, y: 200, w: 500, h: 400 });
    expect(loadOverlayGeometry("execute")).toMatchObject({ x: 10 });
    expect(loadOverlayGeometry("action")).toMatchObject({ x: 300 });
    // The viewer was never moved — it must still use its own default layout.
    expect(loadOverlayGeometry("fullres")).toBeNull();
  });

  it("returns null (=> surface default) for a missing key", () => {
    expect(loadOverlayGeometry("action")).toBeNull();
  });

  it.each([
    ["not json at all", "{{{"],
    ["a non-object", "42"],
    ["a non-numeric position", '{"x":"10","y":10,"w":600,"h":400}'],
    ["NaN-producing nulls in the position", '{"x":null,"y":null,"w":600,"h":400}'],
  ])("discards %s rather than placing the overlay somewhere nobody chose", (_label, raw) => {
    localStorage.setItem(overlayStorageKey("execute"), raw);
    expect(loadOverlayGeometry("execute")).toBeNull();
  });

  it.each([
    ["a missing size", '{"x":10,"y":20}'],
    ["a zero size", '{"x":10,"y":20,"w":0,"h":0}'],
    ["a non-numeric size", '{"x":10,"y":20,"w":"wide","h":null}'],
  ])(
    "keeps the chosen position and degrades %s to auto",
    (_label, raw) => {
      // The position is the part the user chose deliberately; an unusable size
      // must not throw it away — it just means "never resized".
      localStorage.setItem(overlayStorageKey("execute"), raw);
      expect(loadOverlayGeometry("execute")).toEqual({
        x: 10,
        y: 20,
        w: null,
        h: null,
      });
    }
  );

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
    expect(loadOverlayGeometry("action")).toBeNull();
    getSpy.mockRestore();
  });
});
