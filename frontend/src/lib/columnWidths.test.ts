// Unit tests for cross-launch column-width persistence (#685 → s47).

import { describe, it, expect, beforeEach } from "vitest";
import { loadColumnWidths, saveColumnWidths } from "./columnWidths";
import { DEFAULT_COLUMN_WIDTHS } from "./resultColumns";

const STORAGE_KEY = "pm.result-tree.column-widths.v1";

describe("columnWidths persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns the defaults when nothing is persisted", () => {
    expect(loadColumnWidths()).toEqual(DEFAULT_COLUMN_WIDTHS);
  });

  it("round-trips a saved width map merged over the defaults", () => {
    saveColumnWidths({ ...DEFAULT_COLUMN_WIDTHS, name: 333, size: 120 });
    const loaded = loadColumnWidths();
    expect(loaded.name).toBe(333);
    expect(loaded.size).toBe(120);
    // Untouched columns keep their defaults.
    expect(loaded.action).toBe(DEFAULT_COLUMN_WIDTHS.action);
  });

  it("ignores corrupt / non-positive entries (falls back to default per column)", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ name: 0, size: -5, action: "wide", score: NaN, dims: 200 })
    );
    const loaded = loadColumnWidths();
    // Bad entries → default; the one valid positive number is honoured.
    expect(loaded.name).toBe(DEFAULT_COLUMN_WIDTHS.name);
    expect(loaded.size).toBe(DEFAULT_COLUMN_WIDTHS.size);
    expect(loaded.action).toBe(DEFAULT_COLUMN_WIDTHS.action);
    expect(loaded.score).toBe(DEFAULT_COLUMN_WIDTHS.score);
    expect(loaded.dims).toBe(200);
  });

  it("fails open on a malformed JSON blob", () => {
    localStorage.setItem(STORAGE_KEY, "{not valid json");
    expect(loadColumnWidths()).toEqual(DEFAULT_COLUMN_WIDTHS);
  });
});
