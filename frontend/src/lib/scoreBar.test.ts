// Score mini-bar geometry (#878 slice b).
//
// Every case here is a value the bar can actually be handed by a real
// manifest or a real response: `null` for the unscored rows the scorer
// deliberately produces (Live Photo MOV passengers), NaN for a malformed
// number that survives JSON.parse, and out-of-domain values for a manifest
// written by an older scorer. Each one has a wrong answer that is silent:
// a NaN width is invalid CSS, so the fill keeps whatever width it had, and an
// out-of-domain score overflows the cell.

import { describe, it, expect } from "vitest";

import { scoreBarRatio, scoreBarWidth } from "./scoreBar";

describe("scoreBarRatio", () => {
  it("passes an in-domain score through unchanged", () => {
    expect(scoreBarRatio(0.42)).toBe(0.42);
  });

  it("returns 0 for an unscored row (null) so no bar is drawn", () => {
    expect(scoreBarRatio(null)).toBe(0);
    expect(scoreBarRatio(undefined)).toBe(0);
  });

  it("returns 0 for NaN and the infinities instead of an invalid CSS width", () => {
    expect(scoreBarRatio(Number.NaN)).toBe(0);
    expect(scoreBarRatio(Number.POSITIVE_INFINITY)).toBe(0);
    expect(scoreBarRatio(Number.NEGATIVE_INFINITY)).toBe(0);
  });

  it("clamps below 0 and above 1 (the Qt delegate's max(0, min(1, s)) rule)", () => {
    expect(scoreBarRatio(-0.3)).toBe(0);
    expect(scoreBarRatio(1.7)).toBe(1);
    expect(scoreBarRatio(0)).toBe(0);
    expect(scoreBarRatio(1)).toBe(1);
  });
});

describe("scoreBarWidth", () => {
  it("renders the ratio as a CSS percentage", () => {
    expect(scoreBarWidth(0.5)).toBe("50%");
    expect(scoreBarWidth(null)).toBe("0%");
    expect(scoreBarWidth(2)).toBe("100%");
  });
});
