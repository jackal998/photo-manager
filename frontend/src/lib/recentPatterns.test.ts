// recentPatterns.test.ts — #741 sub-item B: persist/read shape + dedup/cap.

import { describe, it, expect } from "vitest";
import {
  parseRecentPatterns,
  recordRecentPattern,
  visibleRecentPatterns,
  RECENT_PATTERNS_CAP,
  type RecentPatternEntry,
} from "./recentPatterns";

describe("parseRecentPatterns", () => {
  it("returns [] for non-array input", () => {
    expect(parseRecentPatterns(null)).toEqual([]);
    expect(parseRecentPatterns(undefined)).toEqual([]);
    expect(parseRecentPatterns("not an array")).toEqual([]);
    expect(parseRecentPatterns({ 0: ["File Name", "IMG"] })).toEqual([]);
  });

  it("accepts well-formed [field, pattern] tuples", () => {
    const raw = [
      ["File Name", "IMG"],
      ["Folder", "^2024"],
    ];
    expect(parseRecentPatterns(raw)).toEqual(raw);
  });

  it("accepts a null field (legacy Qt entry — applies to any field)", () => {
    const raw = [[null, "legacy_pattern"]];
    expect(parseRecentPatterns(raw)).toEqual(raw);
  });

  it("drops entries with the wrong tuple length", () => {
    const raw = [["File Name"], ["File Name", "IMG", "extra"]];
    expect(parseRecentPatterns(raw)).toEqual([]);
  });

  it("drops entries with a non-string/non-null field", () => {
    expect(parseRecentPatterns([[42, "IMG"]])).toEqual([]);
  });

  it("drops entries with an empty-string pattern", () => {
    expect(parseRecentPatterns([["File Name", ""]])).toEqual([]);
  });

  it("drops entries with a non-string pattern", () => {
    expect(parseRecentPatterns([["File Name", 123]])).toEqual([]);
  });

  it("caps at RECENT_PATTERNS_CAP entries even when the raw input has more (#741 — cap on read, not just on write)", () => {
    const raw: RecentPatternEntry[] = [];
    for (let i = 0; i < RECENT_PATTERNS_CAP + 5; i++) {
      raw.push(["File Name", `pattern_${i}`]);
    }
    const result = parseRecentPatterns(raw);
    expect(result).toHaveLength(RECENT_PATTERNS_CAP);
    // parseRecentPatterns doesn't reorder — it keeps the first N entries as
    // given (unlike recordRecentPattern, which pushes newest-first).
    expect(result).toEqual(raw.slice(0, RECENT_PATTERNS_CAP));
  });

  it("keeps the well-formed entries and drops only the malformed ones", () => {
    const raw = [
      ["File Name", "IMG"],
      "legacy bare string", // pre-#347 shape — not accepted here (web has no legacy data)
      ["Folder", ""],
      ["Score", "^abc$"],
    ];
    expect(parseRecentPatterns(raw)).toEqual([
      ["File Name", "IMG"],
      ["Score", "^abc$"],
    ]);
  });
});

describe("recordRecentPattern", () => {
  it("pushes a new entry to the front", () => {
    const result = recordRecentPattern([], "File Name", "IMG");
    expect(result).toEqual([["File Name", "IMG"]]);
  });

  it("trims whitespace before recording", () => {
    const result = recordRecentPattern([], "File Name", "  IMG  ");
    expect(result).toEqual([["File Name", "IMG"]]);
  });

  it("never records an empty (or whitespace-only) pattern — the #397 no-op guard", () => {
    const existing: RecentPatternEntry[] = [["File Name", "IMG"]];
    expect(recordRecentPattern(existing, "File Name", "")).toBe(existing);
    expect(recordRecentPattern(existing, "File Name", "   ")).toBe(existing);
  });

  it("never records a pattern that fails to compile as a regex", () => {
    const existing: RecentPatternEntry[] = [];
    // Unbalanced group — invalid regex.
    expect(recordRecentPattern(existing, "File Name", "(unclosed")).toBe(existing);
  });

  it("dedupes by exact (field, pattern) match, moving the re-applied entry to the front", () => {
    const existing: RecentPatternEntry[] = [
      ["File Name", "IMG"],
      ["File Name", "\\.jpg$"],
    ];
    const result = recordRecentPattern(existing, "File Name", "\\.jpg$");
    expect(result).toEqual([
      ["File Name", "\\.jpg$"],
      ["File Name", "IMG"],
    ]);
  });

  it("does NOT dedupe the same pattern text recorded under a different field", () => {
    const existing: RecentPatternEntry[] = [["Folder", "IMG"]];
    const result = recordRecentPattern(existing, "File Name", "IMG");
    expect(result).toEqual([
      ["File Name", "IMG"],
      ["Folder", "IMG"],
    ]);
  });

  it("caps the list at RECENT_PATTERNS_CAP, dropping the oldest", () => {
    let list: RecentPatternEntry[] = [];
    for (let i = 0; i < RECENT_PATTERNS_CAP + 3; i++) {
      list = recordRecentPattern(list, "File Name", `pattern_${i}`);
    }
    expect(list).toHaveLength(RECENT_PATTERNS_CAP);
    // Most recent first; the oldest 3 (pattern_0..2) fell off the cap.
    expect(list[0]).toEqual(["File Name", `pattern_${RECENT_PATTERNS_CAP + 2}`]);
    expect(list.some(([, p]) => p === "pattern_0")).toBe(false);
  });
});

describe("visibleRecentPatterns", () => {
  it("shows only entries matching the current field", () => {
    const entries: RecentPatternEntry[] = [
      ["File Name", "IMG"],
      ["Folder", "2024"],
    ];
    expect(visibleRecentPatterns(entries, "File Name")).toEqual([
      ["File Name", "IMG"],
    ]);
  });

  it("always shows legacy null-field entries regardless of the current field", () => {
    const entries: RecentPatternEntry[] = [
      ["File Name", "IMG"],
      [null, "legacy"],
    ];
    expect(visibleRecentPatterns(entries, "Folder")).toEqual([[null, "legacy"]]);
  });

  it("returns [] when nothing matches", () => {
    const entries: RecentPatternEntry[] = [["File Name", "IMG"]];
    expect(visibleRecentPatterns(entries, "Folder")).toEqual([]);
  });
});
