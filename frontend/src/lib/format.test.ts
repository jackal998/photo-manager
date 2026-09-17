// Tests for lib/format.ts — focused on similarityLabel's optional `t`
// parameter (web audit fix 5/6: the "Ref" similarity label was hardcoded
// English with no translation hook, since format.ts is a plain function
// module and can't call the useT() hook directly).

import { describe, it, expect, vi } from "vitest";

import {
  similarityLabel,
  classificationLabel,
  formatBytes,
  formatScore,
  formatDate,
  formatDims,
} from "./format";
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
    const t = () => "參考";
    expect(similarityLabel(sim, t)).toBe("參考");
  });

  it("purely symbolic kinds ignore t entirely (percent / none are symbols, not words)", () => {
    const t = vi.fn((_key: string, fallback: string) => fallback);
    expect(similarityLabel({ kind: "percent", percent: 92 }, t)).toBe("92%");
    expect(similarityLabel({ kind: "none", percent: null }, t)).toBe("—");
    expect(t).not.toHaveBeenCalled();
  });

  // Copy audit R6: the near-dup cell rendered a bare hardcoded "~" in both
  // locales, while the desktop catalog (tree.similarity_near_dup) and
  // docs/features.md both document the cell as "~dup".
  it("near_dup renders the translatable '~dup' label, not a bare tilde", () => {
    const t = vi.fn((_key: string, fallback: string) => fallback);
    expect(similarityLabel({ kind: "near_dup", percent: null }, t)).toBe("~dup");
    expect(t).toHaveBeenCalledWith("web.format.similarity_near_dup", "~dup");
  });

  it("near_dup uses the zh_TW catalog value when one is supplied", () => {
    expect(similarityLabel({ kind: "near_dup", percent: null }, () => "~近似")).toBe(
      "~近似"
    );
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

// Copy audit R1 (values half): the Action column rendered the scanner's
// classification enum verbatim, so a zh_TW user read "REVIEW_DUPLICATE" the
// same as an en user did. The bug a user hits is the raw enum reaching the
// screen — and, on the other side, a NEW classifier value silently rendering
// blank because nobody added a catalog key for it.
describe("classificationLabel", () => {
  it("never returns a raw enum value for a value the scanner emits today", () => {
    // scanner/dedup.py + core/app_service/review_view.py's _ACTION_SORT.
    for (const raw of ["EXACT", "REVIEW_DUPLICATE", "KEEP", "UNDATED"]) {
      expect(classificationLabel(raw)).not.toBe(raw);
      expect(classificationLabel(raw)).not.toBe("");
    }
  });

  it("asks the catalog for each value's own key, and renders what it gets", () => {
    const t = vi.fn((_key: string, fallback: string) => fallback);
    expect(classificationLabel("REVIEW_DUPLICATE", t)).toBe("Near-duplicate");
    expect(t).toHaveBeenCalledWith(
      "web.classification.review_duplicate",
      "Near-duplicate"
    );
    expect(classificationLabel("EXACT", () => "完全相同")).toBe("完全相同");
  });

  it("renders the em dash for a row with no classification", () => {
    expect(classificationLabel("")).toBe("—");
  });

  it("shows an UNKNOWN value verbatim rather than blanking the cell", () => {
    // A classifier value added without a catalog key must stay visible — a
    // silent blank would hide the drift from everyone including this test.
    expect(classificationLabel("BRAND_NEW_KIND")).toBe("BRAND_NEW_KIND");
  });
});
