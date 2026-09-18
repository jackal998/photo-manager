// Render tests for the Daylight file row (#878): the 5-state similarity badge
// and the delete-row treatment. Rendered directly with explicit props, the way
// ColumnHeaderRow.test.tsx does — no store, no virtualizer.

import type { ComponentProps } from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { FileRow } from "./FileRow";
import type { FileRow as FileRowData, Similarity } from "@/api/types";
import { DEFAULT_COLUMN_WIDTHS, type ColumnId } from "@/lib/resultColumns";
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

function renderRow(
  row: FileRowData,
  overrides: Partial<ComponentProps<typeof FileRow>> = {}
) {
  render(
    <FileRow
      row={row}
      groupId={GROUP_ID}
      groupNumber={1}
      columnWidths={DEFAULT_COLUMN_WIDTHS}
      onDecision={vi.fn()}
      onLock={vi.fn()}
      {...overrides}
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

  // Copy audit R7 / design Q2 (2026-09-18): the keeper row used to say "Ref"
  // TWICE — a gold pill beside the filename and the ★ badge in the Similarity
  // column, both from the same catalog key. Two identical cues on one row
  // train the eye to ignore both.
  it("says 'Ref' exactly once on a keeper row — the Similarity badge", () => {
    const rowEl = renderRow(
      makeRow({ similarity: { kind: "ref", percent: null }, is_ref_winner: true })
    );
    const refCues = within(rowEl).getAllByText("Ref");
    expect(refCues).toHaveLength(1);
    expect(badgeOf(rowEl).contains(refCues[0])).toBe(true);
  });

  it("marks the keeper with a left accent strip and a heavier name than its peers", () => {
    const keeper = renderRow(
      makeRow({ similarity: { kind: "ref", percent: null }, is_ref_winner: true })
    );
    const peer = renderRow(
      makeRow({ basename: "peer.jpg", file_path: "/photos/peer.jpg" })
    );
    // The strip is the keeper's only left-hand cue now the pill is gone; the
    // 2px gutter itself is on every row so the keeper costs no layout shift.
    // The strip is the ACCENT token (Q2), not the Ref badge's own ink: at
    // #a85f2e it sat 5/255 from the group strip's #a85a2c directly above it,
    // which reads as a rendering fault rather than two distinct cues.
    expect(keeper.className).toContain("border-l-warm");
    expect(keeper.className).not.toContain("border-l-sim-ref-ink");
    expect(peer.className).toContain("border-l-2");
    expect(peer.className).not.toContain("border-l-warm");

    expect(within(keeper).getByText("dup.jpg").className).toContain("font-semibold");
    expect(within(peer).getByText("peer.jpg").className).toContain("font-medium");
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

describe("FileRow score mini bar (#878)", () => {
  function scoreCell(rowEl: HTMLElement): HTMLElement {
    const cell = rowEl.querySelector<HTMLElement>('[data-col="score"]');
    if (cell === null) throw new Error("no score cell rendered");
    return cell;
  }

  it("keeps the score cell's text exactly the formatted number", () => {
    // The parity counter and every scenario read this cell by its TEXT. The
    // bar must therefore contribute none — if it ever renders a label, a
    // percentage or a title, the cell stops matching its desktop counterpart
    // and nothing else in the suite would notice.
    const rowEl = renderRow(makeRow({ score: 0.64 }));
    expect(scoreCell(rowEl).textContent).toBe("0.6");
  });

  it("fills the track to the score's fraction", () => {
    const fill = scoreCell(renderRow(makeRow({ score: 0.25 }))).querySelector(
      "[data-score-fill]"
    ) as HTMLElement;
    expect(fill.style.width).toBe("25%");
  });

  it("draws no track at all for an unscored row", () => {
    // score === null is "not a ranking candidate" (a Live Photo MOV
    // passenger), not "scored zero" — an empty track would read as the
    // latter.
    const cell = scoreCell(renderRow(makeRow({ score: null })));
    expect(cell.querySelector("[data-score-track]")).toBeNull();
    expect(cell.textContent).toBe("—");
  });
});

describe("FileRow Action column = the decision (Q1)", () => {
  it("puts the decision control inside the Action column, not beside it", () => {
    const row = renderRow(makeRow());
    const actionCell = row.querySelector<HTMLElement>('[data-col="action"]');
    expect(actionCell).not.toBeNull();
    expect(
      within(actionCell!).getByTestId(`row-decision-${GROUP_ID}-dup.jpg`)
    ).toBeInTheDocument();
  });

  it("no longer prints the classification as a column", () => {
    // `REVIEW_DUPLICATE` used to render as "Near-duplicate" in its own column
    // right next to the unlabelled decision control — two things that looked
    // like the decision. The Action cell must now contain the control and none
    // of that text.
    const row = renderRow(makeRow({ action: "REVIEW_DUPLICATE" }));
    const actionCell = row.querySelector<HTMLElement>('[data-col="action"]');
    expect(actionCell!.textContent).not.toContain("Near-duplicate");
    expect(row.querySelector('[data-col="classification"]')).toBeNull();
  });

  it("keeps the classification reachable on the similarity badge's tooltip", () => {
    const row = renderRow(makeRow({ action: "EXACT" }));
    expect(badgeOf(row)).toHaveAttribute("title", "Exact copy");
  });

  it("says nothing in the tooltip for a row with no classification", () => {
    // The ref-tier keeper carries action='' — an em dash tooltip, not a stray
    // enum. (`classificationLabel('')` is the em dash.)
    const row = renderRow(makeRow({ action: "" }));
    expect(badgeOf(row)).toHaveAttribute("title", "—");
  });
});

describe("FileRow column shedding (L3 budget)", () => {
  it("drops the dims and date cells the table width cannot afford", () => {
    const row = renderRow(makeRow(), {
      visibleCols: new Set<ColumnId>(["name", "similarity", "action", "score", "size"]),
    });
    expect(row.querySelector('[data-col="dims"]')).toBeNull();
    expect(row.querySelector('[data-col="date"]')).toBeNull();
    // Size and the decision survive — shedding is a budget, not a reset.
    expect(row.querySelector('[data-col="size"]')).not.toBeNull();
    expect(row.querySelector('[data-col="action"]')).not.toBeNull();
  });

  it("renders every cell when the table width is unmeasured", () => {
    const row = renderRow(makeRow());
    expect(row.querySelector('[data-col="dims"]')).not.toBeNull();
    expect(row.querySelector('[data-col="date"]')).not.toBeNull();
  });

  it("narrows the score cell below the compact threshold", () => {
    const row = renderRow(makeRow(), { scoreCompact: true });
    const cell = row.querySelector<HTMLElement>('[data-col="score"]')!;
    expect(cell.style.width).toBe("72px");
    expect(cell).toHaveAttribute("data-col-compact");
  });
});

describe("FileRow cell typography (REPLY L2)", () => {
  it("renders the machine-value cells in mono, right-aligning the magnitudes", () => {
    // Digits that do not line up down the column are the cheapest legibility
    // loss on this screen — and the registry is the single source both this row
    // and the header read, so a drift here is a drift everywhere.
    const row = renderRow(makeRow());
    for (const id of ["size", "dims", "score", "date"] as const) {
      expect(row.querySelector(`[data-col="${id}"]`)!.className).toContain("font-mono");
    }
    for (const id of ["size", "dims", "score"] as const) {
      expect(row.querySelector(`[data-col="${id}"]`)!.className).toContain("text-right");
    }
    expect(row.querySelector('[data-col="date"]')!.className).not.toContain("text-right");
    expect(row.querySelector('[data-col="name"]')!.className).not.toContain("font-mono");
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
