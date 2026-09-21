// Unit tests for cross-launch preview-panel-width persistence (#739 → s39).

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  loadPanelWidths,
  savePanelWidths,
  clampPanelWidth,
  DEFAULT_PANEL_WIDTHS,
  LEGACY_DEFAULT_PANEL_WIDTHS,
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

  // ------------------------------------------------------------------------
  // Layout slice PV — the 288 → 320 default move (REPLY P1)
  // ------------------------------------------------------------------------

  it("the default is 320, and the legacy default it replaces is 288", () => {
    // Pinned as numbers, not as "whatever the module says": the whole point of
    // the migration below is that these two are DIFFERENT, and a test written
    // against the constants alone would keep passing if they were made equal.
    expect(DEFAULT_PANEL_WIDTHS.preview).toBe(320);
    expect(LEGACY_DEFAULT_PANEL_WIDTHS.preview).toBe(288);
  });

  it("migrates a persisted OLD DEFAULT (288) to the new default", () => {
    // The drag handler writes the whole map on any resize, so most browsers
    // that ever touched the splitter carry an explicit 288 that nobody chose.
    // Without this, the new default reaches only brand-new browsers and the
    // pane the REPLY specced at 320 ships at 288 for every existing user.
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ preview: 288 }));
    expect(loadPanelWidths().preview).toBe(320);
  });

  it("keeps a width the user actually chose", () => {
    // The false-positive half: a migration that rewrote every stored value
    // would look identical to a working one on the test above, and would throw
    // away the resize P1 explicitly protects («a persisted user value wins»).
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ preview: 420 }));
    expect(loadPanelWidths().preview).toBe(420);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ preview: 289 }));
    expect(loadPanelWidths().preview).toBe(289);
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
