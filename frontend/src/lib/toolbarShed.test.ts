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

// #918 — the second stage folds Set Action…, the language toggle and Settings
// into the "⋯" menu. Without it the strip measured 127px over at 1000px on
// CI's wide (non-Segoe) stack and the Delete CTA sat off the end of a
// scrolling toolbar.
describe("toolbarShedPlan — second stage (the ⋯ overflow menu)", () => {
  it("folds the three controls one pixel below the threshold", () => {
    expect(toolbarShedPlan(1099).overflowMenu).toBe(true);
    expect(toolbarShedPlan(1000).overflowMenu).toBe(true);
    expect(toolbarShedPlan(600).overflowMenu).toBe(true);
  });

  it("keeps them inline at and above it", () => {
    expect(toolbarShedPlan(1100).overflowMenu).toBe(false);
    expect(toolbarShedPlan(1280).overflowMenu).toBe(false);
  });

  it("never folds on a missing measurement", () => {
    // Same rule as the first stage: an unmeasured strip is not a narrow one,
    // and folding would hide Settings behind a menu on every first paint.
    expect(toolbarShedPlan(null).overflowMenu).toBe(false);
    expect(toolbarShedPlan(0).overflowMenu).toBe(false);
    expect(toolbarShedPlan(Number.NaN).overflowMenu).toBe(false);
  });

  it("leaves no width where only the manifest pair has shed", () => {
    // The manifest shed alone needs ~1127px on the wide stack — more than its
    // own 1100 threshold — so any width that sheds the pair but keeps the
    // three controls inline is a width where the strip still scrolls.
    for (const w of [1099, 1098, 1050, 1000, 900]) {
      const plan = toolbarShedPlan(w);
      expect(plan.manifestOpen || plan.overflowMenu).toBe(true);
    }
  });
});
