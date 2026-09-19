// Unit tests for the result-tree column registry + sort comparators (#685).

import { describe, it, expect } from "vitest";
import {
  COLUMNS,
  DEFAULT_COLUMN_WIDTHS,
  clampResizeWidth,
  columnCellStyle,
  columnResizeCeiling,
  effectiveColumnWidths,
  isScoreCompact,
  makeRowComparator,
  requiredWidth,
  shedThresholds,
  visibleColumns,
  type ColumnId,
} from "./resultColumns";
import type { FileRow as FileRowData } from "@/api/types";

// Minimal FileRow stub — the comparators only read basename + file_size_bytes.
function mk(basename: string, size: number): FileRowData {
  return { basename, file_size_bytes: size } as unknown as FileRowData;
}

describe("resultColumns registry", () => {
  it("derives DEFAULT_COLUMN_WIDTHS from COLUMNS (one entry per column)", () => {
    const ids = COLUMNS.map((c) => c.id).sort();
    const keys = (Object.keys(DEFAULT_COLUMN_WIDTHS) as ColumnId[]).sort();
    expect(keys).toEqual(ids);
    for (const c of COLUMNS) {
      expect(DEFAULT_COLUMN_WIDTHS[c.id]).toBe(c.defaultWidth);
    }
  });

  it("marks exactly File Name and Size sortable (s45 scope)", () => {
    const sortable = COLUMNS.filter((c) => c.sortable).map((c) => c.id);
    expect(sortable.sort()).toEqual(["name", "size"]);
  });

  it("heads the decision column 'Action' and registers no classification column (Q1)", () => {
    // The decision control's column IS `action` — the classification enum that
    // used to print there has no column of its own any more. If a later change
    // re-points this label or re-adds a classification entry, the row goes back
    // to showing two things that look like the decision.
    const action = COLUMNS.find((c) => c.id === "action");
    expect(action?.labelKey).toBe("web.column.action");
    expect(action?.labelFallback).toBe("Action");
    expect(COLUMNS.map((c) => c.id)).not.toContain("classification");
  });

  it("gives the machine-value columns mono + right alignment (REPLY L2)", () => {
    const mono = COLUMNS.filter((c) => c.mono).map((c) => c.id);
    expect(mono.sort()).toEqual(["date", "dims", "score", "size"]);
    const right = COLUMNS.filter((c) => c.align === "right").map((c) => c.id);
    // Size and dims right-align so magnitudes compare down the column; date is
    // mono but stays left (it is a fixed-width label, not a magnitude).
    expect(right.sort()).toEqual(["dims", "score", "size"]);
  });

  it("makes exactly File Name the fill column, floored at its own stored width", () => {
    // Shedding exists to feed this column. With nothing flexible, a shed trades
    // a small overflow for a large dead gutter and the filename — the one
    // string the user actually reads — gains nothing.
    expect(COLUMNS.filter((c) => c.flexible).map((c) => c.id)).toEqual(["name"]);
    const name = COLUMNS.find((c) => c.id === "name")!;
    // The stored width is BOTH the basis and the floor: the box fills slack but
    // can never render narrower than the width the user chose.
    expect(columnCellStyle(name, 240)).toEqual({
      flexGrow: 1,
      flexShrink: 1,
      flexBasis: 240,
      minWidth: 240,
    });
    expect(columnCellStyle(COLUMNS.find((c) => c.id === "size")!, 72)).toEqual({ width: 72 });
  });

  it("uses the L3 column-budget widths", () => {
    expect(DEFAULT_COLUMN_WIDTHS).toEqual({
      name: 160,
      similarity: 92,
      action: 168,
      score: 96,
      dims: 88,
      size: 72,
      date: 112,
    });
  });
});

describe("visibleColumns — need-based shedding", () => {
  // Every boundary below is DERIVED from the registry + the row chrome, never
  // typed in. The design reply quotes 1200 / 1080 / 940px, but those are an
  // output of its own budget arithmetic with its own widths — pinning them as
  // literals would make the table shed a column it could afford the moment a
  // width or the gutter changes, and no test would notice.
  const T = shedThresholds();
  const ids = (w: number | null) => visibleColumns(w).map((c) => c.id);

  it("derives each threshold as the width that just fits the set above it", () => {
    const withoutDims = COLUMNS.filter((c) => c.id !== "dims");
    const withoutDate = withoutDims.filter((c) => c.id !== "date");
    expect(T.dims).toBe(requiredWidth(COLUMNS, DEFAULT_COLUMN_WIDTHS));
    expect(T.date).toBe(requiredWidth(withoutDims, DEFAULT_COLUMN_WIDTHS));
    expect(T.scoreCompact).toBe(requiredWidth(withoutDate, DEFAULT_COLUMN_WIDTHS));
    // Each step must actually free width, or shedding would loop without
    // getting anywhere.
    expect(T.dims).toBeGreaterThan(T.date);
    expect(T.date).toBeGreaterThan(T.scoreCompact);
  });

  it("renders every column when the width has not been measured yet", () => {
    // A 0-width box (first paint / headless layout) must never read as a narrow
    // table, or the columns vanish on a viewport nobody ever narrowed.
    expect(ids(null)).toEqual(COLUMNS.map((c) => c.id));
  });

  it("sheds NOTHING while the row fits — at its threshold and above", () => {
    expect(ids(T.dims)).toEqual(COLUMNS.map((c) => c.id));
    expect(ids(T.dims + 400)).toEqual(COLUMNS.map((c) => c.id));
  });

  it("drops dims one pixel below its threshold, and nothing else", () => {
    const at = ids(T.dims - 1);
    expect(at).not.toContain("dims");
    expect(at).toContain("date");
    expect(at).toContain("name");
  });

  it("keeps date until the shed set itself stops fitting", () => {
    expect(ids(T.date)).toContain("date");
    const below = ids(T.date - 1);
    expect(below).not.toContain("date");
    expect(below).not.toContain("dims");
  });

  it("sheds LESS when the user's own widths leave room for more", () => {
    // The point of need-based: at a width that drops dims with the default
    // widths, a narrower Action column pays for dims outright.
    const width = T.dims - 1;
    expect(visibleColumns(width, DEFAULT_COLUMN_WIDTHS).map((c) => c.id)).not.toContain("dims");
    const narrowAction = { ...DEFAULT_COLUMN_WIDTHS, action: DEFAULT_COLUMN_WIDTHS.action - 40 };
    expect(visibleColumns(width, narrowAction).map((c) => c.id)).toContain("dims");
  });

  it("sheds MORE when the user widens a column past what the row can pay for", () => {
    const wideName = { ...DEFAULT_COLUMN_WIDTHS, name: DEFAULT_COLUMN_WIDTHS.name + 200 };
    const at = visibleColumns(T.dims, wideName).map((c) => c.id);
    expect(at).not.toContain("dims");
  });

  it("never sheds a sortable column — a shed can not strand an active sort", () => {
    for (const w of [T.dims + 400, T.dims - 1, T.date - 1, T.scoreCompact - 1, 320]) {
      const at = ids(w);
      expect(at).toContain("name");
      expect(at).toContain("size");
    }
  });
});

describe("isScoreCompact", () => {
  const T = shedThresholds();

  it("is false while the shed set still fits, true one pixel below", () => {
    expect(isScoreCompact(T.scoreCompact)).toBe(false);
    expect(isScoreCompact(T.scoreCompact - 1)).toBe(true);
  });

  it("is false when the width has not been measured", () => {
    expect(isScoreCompact(null)).toBe(false);
  });
});

describe("columnResizeCeiling / clampResizeWidth — a drag can not shed its own column", () => {
  // The bug this exists for: at a table width where Shot Date is the last thing
  // that fits, dragging its handle right pushed it past the budget, the column
  // unmounted under the cursor, and mouseup persisted the oversized width. With
  // no header cell there is no handle and no double-click target, so the column
  // stayed gone at that window size until the user narrowed the preview pane.
  const TABLE = 986; // measured at 1280×800 with the preview open

  it("caps a sheddable column at the width where it still fits", () => {
    // date's ceiling is measured against the set it survives in — dims is
    // already shed at this table width, so its width is not in the sum.
    const withoutDims = COLUMNS.filter((c) => c.id !== "dims");
    const expected =
      TABLE - (requiredWidth(withoutDims, DEFAULT_COLUMN_WIDTHS) - DEFAULT_COLUMN_WIDTHS.date);
    expect(columnResizeCeiling("date", TABLE, DEFAULT_COLUMN_WIDTHS)).toBe(expected);
    expect(clampResizeWidth("date", 312, TABLE, DEFAULT_COLUMN_WIDTHS)).toBe(expected);
    // The capped width must actually keep the column on the row.
    const after = { ...DEFAULT_COLUMN_WIDTHS, date: expected };
    expect(visibleColumns(TABLE, after).map((c) => c.id)).toContain("date");
  });

  it("leaves a narrowing drag alone", () => {
    expect(clampResizeWidth("date", 80, TABLE, DEFAULT_COLUMN_WIDTHS)).toBe(80);
  });

  it("does not cap a NON-sheddable column — widening name may cost date its place", () => {
    expect(columnResizeCeiling("name", TABLE, DEFAULT_COLUMN_WIDTHS)).toBeNull();
    expect(clampResizeWidth("name", 900, TABLE, DEFAULT_COLUMN_WIDTHS)).toBe(900);
  });

  it("does not cap anything before the table has been measured", () => {
    expect(clampResizeWidth("date", 999, null, DEFAULT_COLUMN_WIDTHS)).toBe(999);
  });
});

describe("effectiveColumnWidths — a stored width can not hide its own column", () => {
  const TABLE = 986;

  it("falls back to the default for a sheddable column its width would hide", () => {
    const stale = { ...DEFAULT_COLUMN_WIDTHS, date: 400 };
    expect(visibleColumns(TABLE, stale).map((c) => c.id)).toContain("date");
    expect(effectiveColumnWidths(TABLE, stale).date).toBe(DEFAULT_COLUMN_WIDTHS.date);
    // Only the offending column is touched.
    expect(effectiveColumnWidths(TABLE, stale).name).toBe(stale.name);
  });

  it("leaves a stored width alone when it is not the reason the column is gone", () => {
    // 606px cannot fit Shot Date at ANY width, so nothing is healed and the
    // user's own number survives for the next time the window is wide enough.
    const stale = { ...DEFAULT_COLUMN_WIDTHS, date: 400 };
    expect(effectiveColumnWidths(606, stale)).toEqual(stale);
  });

  it("leaves a width that fits completely alone", () => {
    expect(effectiveColumnWidths(TABLE, DEFAULT_COLUMN_WIDTHS)).toEqual(
      DEFAULT_COLUMN_WIDTHS
    );
    expect(effectiveColumnWidths(null, { ...DEFAULT_COLUMN_WIDTHS, date: 400 }).date).toBe(400);
  });
});

describe("requiredWidth", () => {
  it("counts the chrome, one gap per pair, and the compact score cell", () => {
    // The chrome constants are mirrored from FileRow/ColumnHeaderRow markup —
    // this is the test that notices when the markup moves and they do not.
    const full = requiredWidth(COLUMNS, DEFAULT_COLUMN_WIDTHS);
    const compact = requiredWidth(COLUMNS, DEFAULT_COLUMN_WIDTHS, true);
    expect(full - compact).toBe(DEFAULT_COLUMN_WIDTHS.score - 72);
    // Dropping one column removes its width AND the gap that went with it.
    const oneFewer = requiredWidth(
      COLUMNS.filter((c) => c.id !== "dims"),
      DEFAULT_COLUMN_WIDTHS
    );
    expect(full - oneFewer).toBe(DEFAULT_COLUMN_WIDTHS.dims + 12);
  });
});

describe("makeRowComparator", () => {
  it("returns null when no sort column is set (default = server order)", () => {
    expect(makeRowComparator(null, "asc")).toBeNull();
  });

  it("returns null for a non-sortable column", () => {
    // similarity is headed but not sortable — no comparator.
    expect(makeRowComparator("similarity" as ColumnId, "asc")).toBeNull();
  });

  it("sorts File Name ascending, case-folded (a before B)", () => {
    const cmp = makeRowComparator("name", "asc")!;
    const rows = [mk("B.jpg", 1), mk("a.jpg", 2), mk("C.jpg", 3)];
    const out = [...rows].sort(cmp).map((r) => r.basename);
    expect(out).toEqual(["a.jpg", "B.jpg", "C.jpg"]);
  });

  it("sorts File Name descending on a repeat (direction flip)", () => {
    const cmp = makeRowComparator("name", "desc")!;
    const rows = [mk("a.jpg", 1), mk("c.jpg", 2), mk("b.jpg", 3)];
    const out = [...rows].sort(cmp).map((r) => r.basename);
    expect(out).toEqual(["c.jpg", "b.jpg", "a.jpg"]);
  });

  it("sorts Size numerically, not lexicographically", () => {
    const cmp = makeRowComparator("size", "asc")!;
    // Lexicographic would put "1048576" before "512000"; numeric must not.
    const rows = [mk("big", 1_048_576), mk("small", 512_000), mk("mid", 900_000)];
    const out = [...rows].sort(cmp).map((r) => r.basename);
    expect(out).toEqual(["small", "mid", "big"]);
  });

  it("Size descending reverses the numeric order", () => {
    const cmp = makeRowComparator("size", "desc")!;
    const rows = [mk("small", 512_000), mk("big", 1_048_576)];
    const out = [...rows].sort(cmp).map((r) => r.basename);
    expect(out).toEqual(["big", "small"]);
  });
});
