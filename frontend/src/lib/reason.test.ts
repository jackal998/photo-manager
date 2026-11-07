// reason.ts — the badge tooltip, the decision chip and the confidence label
// (#1047). Pure functions, so no React and no store: the English fallbacks
// ARE the strings under test, the way format.test.ts drives its formatters.

import { describe, it, expect } from "vitest";

import type { FileRow } from "@/api/types";
import {
  confidenceLabel,
  rowTooltip,
  verdictChip,
  verdictChipLabel,
} from "./reason";

function makeRow(overrides: Partial<FileRow> = {}): FileRow {
  return {
    file_path: "/photos/a.jpg",
    basename: "a.jpg",
    folder: "/photos",
    action: "REVIEW_DUPLICATE",
    user_decision: "",
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "percent", percent: 95 },
    score: null,
    file_size_bytes: 1024,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 3,
    thumbnail_url: "",
    ...overrides,
  };
}

describe("verdictChip", () => {
  it("verdictChip prefers keep_all over low", () => {
    // A burst member that is also low-confidence: "keep all" is the decision
    // that changes what the user does, so it wins the one chip slot.
    const row = makeRow({ keep_all: true, match_confidence: "low" });
    expect(verdictChip(row)).toBe("keep_all");
  });

  it("verdictChip is null for a high-confidence row", () => {
    const row = makeRow({ match_confidence: "high", keep_all: false });
    expect(verdictChip(row)).toBeNull();
  });

  it("verdictChip is null for a pre-#1047 row that carries no fields", () => {
    expect(verdictChip(makeRow())).toBeNull();
  });
});

describe("verdictChipLabel", () => {
  it("names both chips in the English fallback", () => {
    expect(verdictChipLabel("keep_all")).toBe("Keep all · burst");
    expect(verdictChipLabel("low_confidence")).toBe("Low confidence");
  });
});

describe("rowTooltip", () => {
  it("rowTooltip puts classification, reason and confidence on separate lines", () => {
    const row = makeRow({
      action: "REVIEW_DUPLICATE",
      reason: "near-duplicate (hamming=3)",
      match_confidence: "high",
    });
    expect(rowTooltip(row)).toBe(
      "Near-duplicate\nnear-duplicate (hamming=3)\nConfidence: high"
    );
  });

  it("rowTooltip omits 'unique' and empty reasons", () => {
    const unique = makeRow({ action: "", reason: "unique" });
    expect(rowTooltip(unique)).toBe("—");
    const empty = makeRow({ action: "", reason: "" });
    expect(rowTooltip(empty)).toBe("—");
  });
});

describe("rowTooltip confidence_level (PR-3)", () => {
  it("rowTooltip prefers confidence_level and labels medium", () => {
    // A REVIEW_DUPLICATE row with match_confidence "high" is medium by level.
    const row = makeRow({
      action: "REVIEW_DUPLICATE",
      match_confidence: "high",
      confidence_level: "medium",
    });
    expect(rowTooltip(row)).toContain("Confidence: medium");
    expect(rowTooltip(row)).not.toContain("Confidence: high");
  });
});

describe("confidenceLabel", () => {
  it("confidenceLabel prints an unknown level verbatim", () => {
    // A new classifier level must surface, not vanish into a blank tooltip.
    expect(confidenceLabel("medium")).toBe("medium");
    expect(confidenceLabel("high")).toBe("Confidence: high");
  });
});
