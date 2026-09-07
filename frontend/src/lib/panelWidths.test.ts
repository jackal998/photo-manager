// Unit tests for cross-launch preview-panel-width persistence (#739 → s39).

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  loadPanelWidths,
  savePanelWidths,
  clampPanelWidth,
  DEFAULT_PANEL_WIDTHS,
  MIN_PANEL_WIDTH,
} from "./panelWidths";

const STORAGE_KEY = "panelWidths";

describe("panelWidths persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns the defaults when nothing is persisted", () => {
    expect(loadPanelWidths()).toEqual(DEFAULT_PANEL_WIDTHS);
  });

  it("round-trips a saved width (set width -> assert localStorage written; simulate load -> assert restored)", () => {
    savePanelWidths({ preview: 420 });
    const raw = localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string)).toEqual({ preview: 420 });

    const loaded = loadPanelWidths();
    expect(loaded.preview).toBe(420);
  });

  it("ignores corrupt / non-positive entries (falls back to default)", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ preview: -5 })
    );
    expect(loadPanelWidths().preview).toBe(DEFAULT_PANEL_WIDTHS.preview);
  });

  it("fails open on a malformed JSON blob", () => {
    localStorage.setItem(STORAGE_KEY, "{not valid json");
    expect(loadPanelWidths()).toEqual(DEFAULT_PANEL_WIDTHS);
  });
});

describe("clampPanelWidth", () => {
  const originalInnerWidth = window.innerWidth;

  afterEach(() => {
    Object.defineProperty(window, "innerWidth", {
      value: originalInnerWidth,
      configurable: true,
    });
  });

  it("floors at MIN_PANEL_WIDTH", () => {
    expect(clampPanelWidth(0)).toBe(MIN_PANEL_WIDTH);
    expect(clampPanelWidth(-100)).toBe(MIN_PANEL_WIDTH);
    expect(clampPanelWidth(150)).toBe(MIN_PANEL_WIDTH);
  });

  it("passes through values inside the bounds (rounded)", () => {
    Object.defineProperty(window, "innerWidth", {
      value: 1200,
      configurable: true,
    });
    expect(clampPanelWidth(400.4)).toBe(400);
  });

  it("ceils at 60% of the live viewport width", () => {
    Object.defineProperty(window, "innerWidth", {
      value: 1000,
      configurable: true,
    });
    expect(clampPanelWidth(900)).toBe(600);
  });

  it("keeps the MIN_PANEL_WIDTH floor when 60% of a narrow viewport is below it (#739 floor-guard)", () => {
    // 60% of 300 = 180, which is below the 200px floor — the floor must win,
    // so the preview pane can never be forced below MIN_PANEL_WIDTH.
    Object.defineProperty(window, "innerWidth", {
      value: 300,
      configurable: true,
    });
    expect(clampPanelWidth(400)).toBe(MIN_PANEL_WIDTH);
    expect(clampPanelWidth(150)).toBe(MIN_PANEL_WIDTH);
  });
});
