// Unit tests for the pure prune helpers (#686): pref normalization and the
// explicit prune-set arithmetic. (Singleton classification lives on the backend
// — see core/app_service/execute_service.classify_singletons — because the web
// review view never contains single-member groups for the FE to classify.)

import { describe, it, expect } from "vitest";

import { computePruneSet, normalizePrunePref } from "./prune";

describe("normalizePrunePref", () => {
  it("passes through the three canonical strings", () => {
    expect(normalizePrunePref("ask")).toBe("ask");
    expect(normalizePrunePref("always")).toBe("always");
    expect(normalizePrunePref("never")).toBe("never");
  });

  it("falls back to 'ask' for unknown / legacy / null values", () => {
    expect(normalizePrunePref(null)).toBe("ask");
    expect(normalizePrunePref(undefined)).toBe("ask");
    expect(normalizePrunePref(true)).toBe("ask"); // stale legacy boolean
    expect(normalizePrunePref("Always")).toBe("ask"); // wrong case
    expect(normalizePrunePref(42)).toBe("ask");
  });
});

describe("computePruneSet", () => {
  const buckets = { plain: ["/p/a.jpg"], actioned: ["/p/x.jpg"] };

  it("includes a bucket only when its flag is set", () => {
    expect(
      computePruneSet(buckets, {
        prunePlain: true,
        pruneActioned: false,
        lockedToPrune: [],
      })
    ).toEqual(["/p/a.jpg"]);
    expect(
      computePruneSet(buckets, {
        prunePlain: false,
        pruneActioned: true,
        lockedToPrune: [],
      })
    ).toEqual(["/p/x.jpg"]);
  });

  it("folds lockedToPrune in unconditionally (the Qt tail)", () => {
    // Keep-all on both buckets but a lock-confirmed locked singleton still prunes.
    expect(
      computePruneSet(buckets, {
        prunePlain: false,
        pruneActioned: false,
        lockedToPrune: ["/p/lock.jpg"],
      })
    ).toEqual(["/p/lock.jpg"]);
  });

  it("combines all opted-in buckets + locked", () => {
    expect(
      computePruneSet(buckets, {
        prunePlain: true,
        pruneActioned: true,
        lockedToPrune: ["/p/lock.jpg"],
      })
    ).toEqual(["/p/a.jpg", "/p/x.jpg", "/p/lock.jpg"]);
  });
});
