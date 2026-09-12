// Render tests for the Daylight file row (#878): the 5-state similarity badge
// and the delete-row treatment. Rendered directly with explicit props, the way
// ColumnHeaderRow.test.tsx does — no store, no virtualizer.

import { render, screen, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { FileRow } from "./FileRow";
import type { FileRow as FileRowData, Similarity } from "@/api/types";
import { DEFAULT_COLUMN_WIDTHS } from "@/lib/resultColumns";
import {
  SIMILARITY_BADGE,
  similarityBadgeState,
  type SimilarityBadgeState,
} from "@/lib/similarityBadge";
import { similarityLabel } from "@/lib/format";
import { rowFileTestid } from "@/testids";

const GROUP_ID = "1";

function makeRow(overrides: Partial<FileRowData> = {}): FileRowData {
  return {
    file_path: "/photos/dup.jpg",
    basename: "dup.jpg",
    folder: "/photos",
    action: "REVIEW_DUPLICATE",
    user_decision: "",
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "percent", percent: 95 },
    score: 71,
    file_size_bytes: 1_048_576,
    pixel_width: 1920,
    pixel_height: 1080,
    shot_date: null,
    creation_date: null,
    phash: "aabbcc001122",
    hamming_distance: 3,
    thumbnail_url: "/api/image?path=/photos/dup.jpg&size=512",
    ...overrides,
  };
}

function renderRow(row: FileRowData) {
  render(
    <FileRow
      row={row}
      groupId={GROUP_ID}
      groupNumber={1}
      columnWidths={DEFAULT_COLUMN_WIDTHS}
      onDecision={vi.fn()}
      onLock={vi.fn()}
    />
  );
  return screen.getByTestId(rowFileTestid(GROUP_ID, row.basename));
}

/** The badge element for the row — the span carrying the resolved state. */
function badgeOf(rowEl: HTMLElement): HTMLElement {
  const badge = rowEl.querySelector<HTMLElement>("[data-sim-state]");
  if (badge === null) throw new Error("no similarity badge rendered");
  return badge;
}

describe("FileRow similarity badge", () => {
  const cases: [string, Similarity, SimilarityBadgeState][] = [
    ["ref winner", { kind: "ref", percent: null }, "ref"],
    ["byte-identical", { kind: "percent", percent: 100 }, "exact"],
    ["near match", { kind: "percent", percent: 95 }, "near"],
    ["distance-less near match", { kind: "near_dup", percent: null }, "near"],
    ["indirect member", { kind: "passenger", percent: 92 }, "indirect"],
    ["no comparable image", { kind: "none", percent: null }, "none"],
  ];

  it.each(cases)(
    "renders %s with its state's border + weight classes",
    (_name, similarity, expectedState) => {
      const rowEl = renderRow(makeRow({ similarity }));
      const badge = badgeOf(rowEl);
      const spec = SIMILARITY_BADGE[expectedState];

      expect(badge.dataset.simState).toBe(expectedState);
      expect(similarityBadgeState(similarity)).toBe(expectedState);
      // The two colour-free cues have to reach the DOM, not just the spec.
      expect(badge.className).toContain(`border-${spec.border}`);
      expect(badge.className).toContain(spec.weight);
    }
  );

  it("shows the state's glyph — ★ on the Ref badge", () => {
    const rowEl = renderRow(
      makeRow({ similarity: { kind: "ref", percent: null }, is_ref_winner: true })
    );
    expect(badgeOf(rowEl).textContent).toContain("★");
  });

  it.each(cases)(
    "keeps %s's cell text exactly similarityLabel()'s output",
    (_name, similarity) => {
      // The parity counter and the qa drivers read cell text; the glyph is a
      // decorative sibling, never part of the label run.
      const rowEl = renderRow(makeRow({ similarity }));
      const label = similarityLabel(similarity);
      expect(
        within(badgeOf(rowEl)).getByText(label)
      ).toBeInTheDocument();
    }
  );
});

describe("FileRow delete treatment", () => {
  it("strikes the filename through and tints the row when staged for delete", () => {
    const rowEl = renderRow(makeRow({ user_decision: "delete" }));
    const name = screen.getByText("dup.jpg");

    expect(name.className).toContain("line-through");
    expect(rowEl.className).toContain("bg-delete-row");
  });

  it("leaves an undecided row unstruck and untinted", () => {
    const rowEl = renderRow(makeRow({ user_decision: "" }));

    expect(screen.getByText("dup.jpg").className).not.toContain("line-through");
    expect(rowEl.className).not.toContain("bg-delete-row");
  });

  it("does not strike through a row staged for removal from the list", () => {
    // "ignore" is remove-from-list, not delete — striking it would tell the
    // user a file is about to be deleted when it is not (#584 decision model).
    const rowEl = renderRow(makeRow({ user_decision: "ignore" }));

    expect(screen.getByText("dup.jpg").className).not.toContain("line-through");
    expect(rowEl.className).not.toContain("bg-delete-row");
  });
});

describe("FileRow group framing", () => {
  it("closes the group frame under the last child only", () => {
    const last = renderRow(makeRow({ basename: "last.jpg" }));
    expect(last.className).not.toContain("border-b-group-line");

    render(
      <FileRow
        row={makeRow({ basename: "tail.jpg" })}
        groupId={GROUP_ID}
        groupNumber={1}
        columnWidths={DEFAULT_COLUMN_WIDTHS}
        onDecision={vi.fn()}
        onLock={vi.fn()}
        isLastInGroup
      />
    );
    expect(
      screen.getByTestId(rowFileTestid(GROUP_ID, "tail.jpg")).className
    ).toContain("border-b-group-line");
  });
});
