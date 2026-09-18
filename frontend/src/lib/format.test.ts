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
  dateLocaleFor,
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
  });

  // Layout REPLY L2: «two decimals, always». Scores inside one near-duplicate
  // group differ in the SECOND decimal far more often than in the first, so at
  // one decimal the cell printed the same number for every row in the group —
  // exactly the rows the user is being asked to choose between.
  it("formatScore always shows two decimals", () => {
    expect(formatScore(87.25)).toBe("87.25");
    expect(formatScore(0.6)).toBe("0.60");
    expect(formatScore(0.644)).toBe("0.64");
    expect(formatScore(0.647)).toBe("0.65");
  });

  it("formatDate returns an em dash for null", () => {
    expect(formatDate(null)).toBe("—");
  });

  // Layout REPLY L2: «`14 Mar 2021` — no time, no seconds». The locale still
  // chooses the field order; the explicit `locale` argument exists so this
  // assertion does not depend on the machine running it.
  it("formatDate spells the month and drops the time", () => {
    expect(formatDate("2021-03-14T10:30:00", "en-GB")).toBe("14 Mar 2021");
    const local = formatDate("2021-03-14T10:30:00");
    expect(local).toContain("2021");
    expect(local).not.toContain(":");
  });

  // The APP's locale must drive the date, not the browser's. Every call site
  // passed `undefined` until this was fixed, which is why an English UI on a
  // Taiwanese machine printed 「2024年2月1日」 in the Shot Date column — and
  // why nothing caught it: `locale` is optional, so forgetting it throws
  // nothing and looks correct on an en-* dev machine.
  it("dateLocaleFor maps each UI locale to its BCP-47 tag", () => {
    expect(dateLocaleFor("en")).toBe("en-GB");
    expect(dateLocaleFor("zh_TW")).toBe("zh-TW");
    // An unknown app locale falls back to the UI's default, NOT to the
    // browser — the point of the map is that the UI decides.
    expect(dateLocaleFor("klingon")).toBe("en-GB");
  });

  it("formatDate renders the same instant differently per UI locale", () => {
    expect(formatDate("2021-03-14T10:30:00", dateLocaleFor("en"))).toBe(
      "14 Mar 2021"
    );
    expect(formatDate("2021-03-14T10:30:00", dateLocaleFor("zh_TW"))).toBe(
      "2021年3月14日"
    );
  });

  it("formatDims returns an em dash when either dimension is null", () => {
    expect(formatDims(null, 100)).toBe("—");
  });

  // Layout REPLY L2: «`4000×3000`, no spaces around ×».
  it("formatDims joins the two magnitudes with a bare ×", () => {
    expect(formatDims(1920, 1080)).toBe("1920×1080");
    expect(formatDims(4000, 3000)).toBe("4000×3000");
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
