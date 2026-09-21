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

  it("hydrates a STALE blob written by an older column set (layout slice C)", () => {
    // A browser that used the app before the column model changed still holds
    // widths for ids the registry no longer knows — and the risk is real in
    // both directions: an unknown key must not leak into the width map (which
    // would hand a column a width nothing renders, and could resurrect it), and
    // it must not throw or poison a known column with NaN on the way past. s47
    // reloads the page against exactly this blob on a developer's machine.
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        classification: 96, // a column id that no longer exists
        action: 210, // survived the change; still a live id
        similarity: "92px", // stale SHAPE, not just a stale id
        name: 240,
      })
    );
    const loaded = loadColumnWidths();
    expect(Object.keys(loaded).sort()).toEqual(
      Object.keys(DEFAULT_COLUMN_WIDTHS).sort()
    );
    expect(loaded).not.toHaveProperty("classification");
    expect(loaded.action).toBe(210);
    expect(loaded.name).toBe(240);
    expect(loaded.similarity).toBe(DEFAULT_COLUMN_WIDTHS.similarity);
    for (const v of Object.values(loaded)) {
      expect(Number.isFinite(v)).toBe(true);
      expect(v).toBeGreaterThan(0);
    }
  });

  it("fails open on a malformed JSON blob", () => {
    localStorage.setItem(STORAGE_KEY, "{not valid json");
    expect(loadColumnWidths()).toEqual(DEFAULT_COLUMN_WIDTHS);
  });
});
