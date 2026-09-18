// Unit tests for the result-tree column registry + sort comparators (#685).

import { describe, it, expect } from "vitest";
import {
  COLUMNS,
  DEFAULT_COLUMN_WIDTHS,
  SHED_THRESHOLDS,
  isScoreCompact,
  makeRowComparator,
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

describe("visibleColumns — the L3 shedding budget", () => {
  const ids = (w: number | null) => visibleColumns(w).map((c) => c.id);

  it("renders every column when the width has not been measured yet", () => {
    // A 0-width box (first paint / headless layout) must never read as a narrow
    // table, or the columns vanish on a viewport nobody ever narrowed.
    expect(ids(null)).toEqual(COLUMNS.map((c) => c.id));
  });

  it("keeps every column at and above the dims threshold", () => {
    expect(ids(SHED_THRESHOLDS.dims)).toContain("dims");
    expect(ids(1280)).toEqual(COLUMNS.map((c) => c.id));
  });

  it("drops dims one pixel below its threshold, and nothing else", () => {
    const at = ids(SHED_THRESHOLDS.dims - 1);
    expect(at).not.toContain("dims");
    expect(at).toContain("date");
    expect(at).toContain("name");
  });

  it("keeps date at its threshold and drops it one pixel below", () => {
    expect(ids(SHED_THRESHOLDS.date)).toContain("date");
    const below = ids(SHED_THRESHOLDS.date - 1);
    expect(below).not.toContain("date");
    expect(below).not.toContain("dims");
  });

  it("never sheds a sortable column — a shed can not strand an active sort", () => {
    for (const w of [1280, 1199, 1079, 939, 320]) {
      const at = ids(w);
      expect(at).toContain("name");
      expect(at).toContain("size");
    }
  });
});

describe("isScoreCompact", () => {
  it("is false at the threshold and true one pixel below", () => {
    expect(isScoreCompact(SHED_THRESHOLDS.scoreCompact)).toBe(false);
    expect(isScoreCompact(SHED_THRESHOLDS.scoreCompact - 1)).toBe(true);
  });

  it("is false when the width has not been measured", () => {
    expect(isScoreCompact(null)).toBe(false);
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
