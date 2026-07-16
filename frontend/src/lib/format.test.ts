// Tests for lib/format.ts — focused on similarityLabel's optional `t`
// parameter (web audit fix 5/6: the "Ref" similarity label was hardcoded
// English with no translation hook, since format.ts is a plain function
// module and can't call the useT() hook directly).

import { describe, it, expect, vi } from "vitest";

import { similarityLabel, formatBytes, formatScore, formatDate, formatDims } from "./format";
import type { Similarity } from "../api/types";

describe("similarityLabel", () => {
  it("falls back to the English default 'Ref' when no t function is passed", () => {
    const sim: Similarity = { kind: "ref", percent: null };
    expect(similarityLabel(sim)).toBe("Ref");
  });

  it("calls the provided t function with the translation key and English fallback", () => {
    const sim: Similarity = { kind: "ref", percent: null };
    const t = vi.fn((_key: string, fallback: string) => fallback);
    const result = similarityLabel(sim, t);
    expect(t).toHaveBeenCalledWith("web.format.similarity_ref", "Ref");
    expect(result).toBe("Ref");
  });

  it("uses the translated string returned by t, not the English fallback", () => {
    const sim: Similarity = { kind: "ref", percent: null };
    const t = (_key: string, _fallback: string) => "參考";
    expect(similarityLabel(sim, t)).toBe("參考");
  });

  it("non-'ref' kinds ignore t entirely (percent/passenger/near_dup/none are symbols, not words)", () => {
    const t = vi.fn((_key: string, fallback: string) => fallback);
    expect(similarityLabel({ kind: "percent", percent: 92 }, t)).toBe("92%");
    expect(similarityLabel({ kind: "near_dup", percent: null }, t)).toBe("~");
    expect(similarityLabel({ kind: "none", percent: null }, t)).toBe("—");
    expect(t).not.toHaveBeenCalled();
  });
});

// ── Pre-existing formatters — smoke coverage (no i18n involved: units and
// the em-dash placeholder are deliberately not translated, see PreviewPane
// / FileRow call sites). Not previously covered by any test file. ────────

describe("formatBytes / formatScore / formatDate / formatDims (smoke)", () => {
  it("formatBytes uses B/KB/MB/GB thresholds", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
  });

  it("formatScore returns an em dash for null", () => {
    expect(formatScore(null)).toBe("—");
    expect(formatScore(87.25)).toBe("87.3");
  });

  it("formatDate returns an em dash for null", () => {
    expect(formatDate(null)).toBe("—");
  });

  it("formatDims returns an em dash when either dimension is null", () => {
    expect(formatDims(null, 100)).toBe("—");
    expect(formatDims(1920, 1080)).toBe("1920 × 1080");
  });
});
