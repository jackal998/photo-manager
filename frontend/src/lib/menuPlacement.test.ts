// placeMenu (#897) — the row context menu must never put an item outside the
// window. Each case is a window/cursor combination a user can produce by
// right-clicking; the numbers are the file-row menu's real proportions
// (~330 px tall, ~220 px wide) against the 400 px window s73 had to avoid.

import { describe, expect, it } from "vitest";

import { MENU_VIEWPORT_MARGIN as M, placeMenu } from "./menuPlacement";

const MENU = { width: 220, height: 330 };
const WINDOW = { width: 1280, height: 800 };
const SHORT_WINDOW = { width: 620, height: 400 };

describe("placeMenu", () => {
  it("opens below-right of the cursor when it fits — the common case is untouched", () => {
    expect(placeMenu({ x: 100, y: 200 }, MENU, WINDOW)).toEqual({
      left: 100,
      top: 200,
      maxHeight: null,
    });
  });

  it("flips upward when the bottom would overflow and there is room above", () => {
    // Right-click near the bottom of a normal window: the menu's BOTTOM edge
    // sits at the cursor, as Qt's QMenu does.
    const p = placeMenu({ x: 100, y: 700 }, MENU, WINDOW);
    expect(p.top).toBe(700 - MENU.height);
    expect(p.top + MENU.height).toBeLessThanOrEqual(WINDOW.height - M);
    expect(p.maxHeight).toBeNull();
  });

  it("slides up to the bottom edge when it fits the window but neither side of the cursor", () => {
    // 400 px window, cursor mid-height: 330 px fits neither below (200+330)
    // nor above (200-330), but it does fit the window — so no scrolling,
    // just moved until the last item is on screen.
    const p = placeMenu({ x: 100, y: 200 }, MENU, SHORT_WINDOW);
    expect(p.top).toBe(SHORT_WINDOW.height - M - MENU.height);
    expect(p.top).toBeGreaterThanOrEqual(M);
    expect(p.maxHeight).toBeNull();
  });

  it("scrolls inside the window when it is taller than the window itself", () => {
    const tall = { width: 220, height: 500 };
    const p = placeMenu({ x: 100, y: 350 }, tall, SHORT_WINDOW);
    expect(p.top).toBe(M);
    expect(p.maxHeight).toBe(SHORT_WINDOW.height - 2 * M);
    // The visible box ends inside the window, so its last item is one scroll
    // away rather than past the edge.
    expect(p.top + p.maxHeight!).toBeLessThanOrEqual(SHORT_WINDOW.height - M);
  });

  it("flips left when the right edge would overflow", () => {
    const p = placeMenu({ x: 1200, y: 200 }, MENU, WINDOW);
    expect(p.left).toBe(1200 - MENU.width);
  });

  it("slides left to the right edge when neither side of the cursor fits", () => {
    const narrow = { width: 300, height: 800 };
    const p = placeMenu({ x: 150, y: 200 }, MENU, narrow);
    expect(p.left).toBe(narrow.width - M - MENU.width);
    expect(p.left).toBeGreaterThanOrEqual(M);
  });

  it("never places the menu before the margin, even for a window narrower than it", () => {
    const tiny = { width: 180, height: 800 };
    expect(placeMenu({ x: 50, y: 200 }, MENU, tiny).left).toBe(M);
  });
});
