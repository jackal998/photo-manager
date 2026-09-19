import { describe, it, expect } from "vitest";

import { TOOLBAR_MANIFEST_SHED_WIDTH, toolbarShedPlan } from "./toolbarShed";

describe("toolbarShedPlan", () => {
  it("keeps the manifest-open pair at and above the threshold", () => {
    expect(toolbarShedPlan(1100).manifestOpen).toBe(true);
    expect(toolbarShedPlan(1280).manifestOpen).toBe(true);
    expect(toolbarShedPlan(TOOLBAR_MANIFEST_SHED_WIDTH).manifestOpen).toBe(true);
  });

  it("sheds it one pixel below", () => {
    // The boundary is the whole point: an off-by-one here means the pair
    // disappears at a width where the strip still fits, or survives at one
    // where the Delete CTA scrolls off the end.
    expect(toolbarShedPlan(1099).manifestOpen).toBe(false);
    expect(toolbarShedPlan(1000).manifestOpen).toBe(false);
    expect(toolbarShedPlan(600).manifestOpen).toBe(false);
  });

  it("renders EVERYTHING when the width has not been measured", () => {
    // `null` is first paint and headless (jsdom returns 0 from every layout
    // API). A missing measurement must never masquerade as a narrow toolbar
    // and silently remove a control — the same rule slice C's visibleColumns
    // follows, and the reason its own probe reads a real box.
    expect(toolbarShedPlan(null).manifestOpen).toBe(true);
    expect(toolbarShedPlan(0).manifestOpen).toBe(true);
    expect(toolbarShedPlan(Number.NaN).manifestOpen).toBe(true);
  });
});
