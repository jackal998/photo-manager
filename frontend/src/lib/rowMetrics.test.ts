// The virtualiser's row-height contract (#878, layout slice R).
//
// Why this file exists rather than an assertion inside ResultTree's suites:
// `estimateSize` is the ONE number in this feature whose being wrong does not
// turn anything red. The rendered DOM is measured afterwards, so the list
// self-corrects visually; what stays broken is arrow-key scroll-into-view,
// which lands a row partly under the sticky header — and `overscan: 10` hides
// even that until the list is long. Pinning the four answers here is the only
// place the mistake is cheap to catch.

import { describe, it, expect } from "vitest";
import {
  ROW_METRICS,
  GROUP_HEADER_HEIGHT,
  estimateRowSize,
} from "./rowMetrics";

describe("estimateRowSize", () => {
  // Layout REPLY §"Row heights": 72 / 52 totals INCLUDING the 1px bottom
  // border, plus the group's last child carrying +6 / +4px of closing breath.
  it("answers 72 / 78 at the comfortable density", () => {
    expect(estimateRowSize("file", false, "comfortable")).toBe(72);
    expect(estimateRowSize("file", true, "comfortable")).toBe(78);
  });

  it("answers 52 / 56 at the compact density", () => {
    expect(estimateRowSize("file", false, "compact")).toBe(52);
    expect(estimateRowSize("file", true, "compact")).toBe(56);
  });

  // The group header is slice G's row, not slice R's — it must NOT pick up the
  // density or the last-in-group breath just because it shares the callback.
  it("keeps the group header at its own height at either density", () => {
    expect(estimateRowSize("group-header", false, "comfortable")).toBe(
      GROUP_HEADER_HEIGHT
    );
    expect(estimateRowSize("group-header", true, "compact")).toBe(
      GROUP_HEADER_HEIGHT
    );
  });

  // The LITERAL, deliberately: the assertions above compare the function to
  // the constant, so they stay green for any value the constant happens to
  // hold. Layout REPLY §"Group header": «height 44px». `GroupRow.tsx` spells
  // the same number as `h-[44px]`, and the two are only checked against each
  // other by s74's computed-height probe — this is the cheap half of that pair.
  it("estimates the group header at the REPLY's 44px", () => {
    expect(GROUP_HEADER_HEIGHT).toBe(44);
  });
});

describe("ROW_METRICS", () => {
  // The class strings are what actually render; the numbers are what the
  // virtualiser estimates with. If the two disagree, every row is the wrong
  // height by exactly that difference and nothing in the suite notices — so
  // assert that each density's `row`/`rowLast` class carries its own number.
  it("renders each density at the height it estimates", () => {
    for (const density of ["comfortable", "compact"] as const) {
      const m = ROW_METRICS[density];
      expect(m.row).toContain(`h-[${m.height}px]`);
      expect(m.rowLast).toContain(`h-[${m.height + m.lastInGroupExtra}px]`);
      expect(m.thumb).toContain(`h-[${m.thumbWidth}px]`);
      expect(m.thumb).toContain(`w-[${m.thumbWidth}px]`);
      expect(m.lock).toContain(`w-[${m.lockWidth}px]`);
      // The header's spacers ARE the row's thumbnail and padlock boxes; a
      // mismatch slides every column header off its column.
      expect(m.headerThumbSpacer).toBe(`w-[${m.thumbWidth}px]`);
      expect(m.headerLockSpacer).toBe(`w-[${m.lockWidth}px]`);
    }
  });

  it("drops the score label only at the compact density", () => {
    expect(ROW_METRICS.comfortable.showScoreLabel).toBe(true);
    expect(ROW_METRICS.compact.showScoreLabel).toBe(false);
  });
});
