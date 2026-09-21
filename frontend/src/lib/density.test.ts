// Cross-launch persistence for the result-tree row density (#878, slice R).
// Mirrors panelWidths.test.ts — same recipe, same failure modes.

import { describe, it, expect, beforeEach } from "vitest";
import { loadDensity, saveDensity, DEFAULT_DENSITY } from "./density";

const STORAGE_KEY = "density";

describe("density persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to comfortable when nothing is persisted", () => {
    expect(loadDensity()).toBe("comfortable");
    expect(DEFAULT_DENSITY).toBe("comfortable");
  });

  it("round-trips a chosen density through localStorage", () => {
    saveDensity("compact");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("compact");
    expect(loadDensity()).toBe("compact");

    saveDensity("comfortable");
    expect(loadDensity()).toBe("comfortable");
  });

  // A stored value that is not one of the two densities would index
  // ROW_METRICS with `undefined` and every row would render with no height —
  // a blank tree. The bug a user hits: an older build, or a hand-edited
  // localStorage, leaves a stale string behind.
  it("falls back to the default for a value that is not a density", () => {
    localStorage.setItem(STORAGE_KEY, "cozy");
    expect(loadDensity()).toBe("comfortable");

    localStorage.setItem(STORAGE_KEY, "");
    expect(loadDensity()).toBe("comfortable");
  });
});
