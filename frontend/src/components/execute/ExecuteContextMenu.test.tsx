// ExecuteContextMenu placement (#897) — the Execute dialog's row menu must stay
// inside the window like the result-tree menu does. It is `absolute` inside a
// layer over the dialog, so its {x, y} are LAYER-relative: the tests pin both
// the window clamp and the conversion through the layer's on-screen origin.
//
// jsdom does no layout, so the menu's size, the window's and the layer's
// position are stubbed; the placement math itself is tested in
// lib/menuPlacement.test.ts.

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExecuteContextMenu } from "./ExecuteContextMenu";
import { CONTEXT_MENU } from "@/testids";

const restore: Array<() => void> = [];

function stub(target: object, prop: string, value: unknown) {
  const prior = Object.getOwnPropertyDescriptor(target, prop);
  Object.defineProperty(target, prop, { configurable: true, get: () => value });
  restore.push(() => {
    if (prior) Object.defineProperty(target, prop, prior);
    else delete (target as Record<string, unknown>)[prop];
  });
}

/** A 220 x 250 menu in a 1024 x 400 window, its layer's origin at (left, top). */
function layout(layerLeft: number, layerTop: number) {
  stub(window, "innerWidth", 1024);
  stub(window, "innerHeight", 400);
  stub(HTMLElement.prototype, "offsetWidth", 220);
  stub(HTMLElement.prototype, "offsetHeight", 250);
  stub(HTMLElement.prototype, "clientHeight", 250);
  stub(HTMLElement.prototype, "scrollHeight", 250);
  stub(HTMLElement.prototype, "offsetParent", {
    getBoundingClientRect: () => ({ left: layerLeft, top: layerTop }),
  });
}

function renderMenu(x: number, y: number) {
  render(
    <ExecuteContextMenu
      x={x}
      y={y}
      filePath="/photos/a.jpg"
      isLocked={false}
      onClose={vi.fn()}
      onRequestRemove={vi.fn()}
    />
  );
  return screen.getByTestId(CONTEXT_MENU);
}

afterEach(() => {
  while (restore.length > 0) restore.pop()!();
});

describe("ExecuteContextMenu placement (#897)", () => {
  it("flips above the cursor when a right-click near the bottom of a short window would push it off", () => {
    layout(0, 0);
    const menu = renderMenu(100, 350);
    // Bottom edge at the cursor: 350 - 250.
    expect(menu.style.top).toBe("100px");
    expect(menu.style.left).toBe("100px");
  });

  it("clamps in WINDOW coordinates and hands back layer-relative ones", () => {
    // A centred dialog: the layer starts at (200, 50) on screen. A right-click
    // at layer (800, 300) is window (1000, 350) — past both the right edge
    // (1000 + 220 > 1020) and the bottom (350 + 250 > 396).
    layout(200, 50);
    const menu = renderMenu(800, 300);
    // Flipped left and up in window coordinates (780, 100), then back into
    // the layer: (780 - 200, 100 - 50).
    expect(menu.style.left).toBe("580px");
    expect(menu.style.top).toBe("50px");
  });
});
