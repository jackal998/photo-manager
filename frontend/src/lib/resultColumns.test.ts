// Unit tests for the result-tree column registry + sort comparators (#685).

import { describe, it, expect } from "vitest";
import {
  COLUMNS,
  DEFAULT_COLUMN_WIDTHS,
  makeRowComparator,
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
