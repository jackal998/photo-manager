// patternSummary.test.ts — #741 sub-item C: Simple-representable -> summary,
// complex/regex -> raw, numeric threshold/top-N -> decoded summary.

import { describe, it, expect } from "vitest";
import { buildPatternSummary, reverseParseSimple } from "./patternSummary";

// A minimal stand-in for useT()'s t() — returns the interpolated fallback,
// exactly like the real translate() does before/without a loaded catalog.
function fakeT(
  _key: string,
  fallback: string,
  params?: Record<string, string | number>
): string {
  if (!params) return fallback;
  return fallback.replace(/\{(\w+)\}/g, (m, k: string) =>
    k in params ? String(params[k]) : m
  );
}

describe("reverseParseSimple", () => {
  it("parses an empty pattern as contains ''", () => {
    expect(reverseParseSimple("")).toEqual({ op: "contains", text: "" });
  });

  it("parses a plain contains pattern", () => {
    expect(reverseParseSimple("IMG")).toEqual({ op: "contains", text: "IMG" });
  });

  it("parses a starts_with pattern (^ prefix)", () => {
    expect(reverseParseSimple("^IMG")).toEqual({ op: "starts_with", text: "IMG" });
  });

  it("parses an ends_with pattern ($ suffix)", () => {
    expect(reverseParseSimple("\\.jpg$")).toEqual({ op: "ends_with", text: ".jpg" });
  });

  it("parses an exact pattern (both anchors)", () => {
    expect(reverseParseSimple("^photo\\.jpg$")).toEqual({
      op: "exact",
      text: "photo.jpg",
    });
  });

  it("returns null for a complex regex (quantifier)", () => {
    expect(reverseParseSimple("IMG_\\d+")).toBeNull();
  });

  it("returns null for a complex regex (alternation)", () => {
    expect(reverseParseSimple("a|b")).toBeNull();
  });

  it("returns null for a complex regex (character class)", () => {
    expect(reverseParseSimple("[abc]")).toBeNull();
  });

  it("treats an escaped trailing $ as literal, not an anchor", () => {
    // "\$" is an escaped literal dollar sign, not an end-anchor — the whole
    // thing is plain-or-escaped text "contains '$'".
    expect(reverseParseSimple("\\$")).toEqual({ op: "contains", text: "$" });
  });
});

describe("buildPatternSummary", () => {
  it("produces a Simple-mode summary for a Simple-representable pattern", () => {
    const summary = buildPatternSummary("File Name", "IMG", fakeT);
    expect(summary).toBe("File Name contains 'IMG'");
  });

  it("produces a Simple-mode summary for starts_with", () => {
    const summary = buildPatternSummary("File Name", "^IMG", fakeT);
    expect(summary).toBe("File Name starts with 'IMG'");
  });

  it("falls back to the raw-regex summary for a complex pattern", () => {
    const summary = buildPatternSummary("File Name", "IMG_\\d+", fakeT);
    expect(summary).toBe("File Name regex 'IMG_\\d+'");
  });

  it("decodes a numeric threshold pseudo-pattern", () => {
    const summary = buildPatternSummary("Score", "__cmp__:>=:75", fakeT);
    expect(summary).toBe("Score >= 75");
  });

  it("decodes a numeric top-N pseudo-pattern (desc -> Top)", () => {
    const summary = buildPatternSummary("Score", "__top_n__:3:desc", fakeT);
    expect(summary).toBe("Top 3 per group by Score");
  });

  it("decodes a numeric top-N pseudo-pattern (asc -> Bottom)", () => {
    const summary = buildPatternSummary("Score", "__top_n__:5:asc", fakeT);
    expect(summary).toBe("Bottom 5 per group by Score");
  });

  it("never mis-decodes a numeric pattern as a Simple/raw string summary", () => {
    // A value containing ':' (e.g. a date-time threshold) must not confuse
    // the cmp decoder — the SECOND ':' onward is the value, per
    // decode_cmp_pattern's "split(':', 1)" semantics.
    const summary = buildPatternSummary(
      "Creation Date",
      "__cmp__:>=:2026-01-01 12:00:00",
      fakeT
    );
    expect(summary).toBe("Creation Date >= 2026-01-01 12:00:00");
  });
});
