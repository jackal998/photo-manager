import { describe, it, expect } from "vitest";

import { nextSelection } from "./multiSelect";

const ORDER = ["/a.jpg", "/b.jpg", "/c.jpg", "/d.jpg", "/e.jpg"];
const NONE = { ctrl: false, shift: false };
const CTRL = { ctrl: true, shift: false };
const SHIFT = { ctrl: false, shift: true };

describe("nextSelection", () => {
  it("plain click replaces the selection and anchors on the clicked row", () => {
    const r = nextSelection(["/a.jpg", "/b.jpg"], "/a.jpg", "/d.jpg", NONE, ORDER);
    expect(r.selectedPaths).toEqual(["/d.jpg"]);
    expect(r.anchorPath).toBe("/d.jpg");
  });

  it("Ctrl+click adds an un-selected row and re-anchors to it", () => {
    const r = nextSelection(["/a.jpg"], "/a.jpg", "/c.jpg", CTRL, ORDER);
    expect(r.selectedPaths).toEqual(["/a.jpg", "/c.jpg"]);
    expect(r.anchorPath).toBe("/c.jpg");
  });

  it("Ctrl+click removes an already-selected row (toggle off) and re-anchors", () => {
    const r = nextSelection(["/a.jpg", "/c.jpg"], "/a.jpg", "/c.jpg", CTRL, ORDER);
    expect(r.selectedPaths).toEqual(["/a.jpg"]);
    expect(r.anchorPath).toBe("/c.jpg");
  });

  it("Shift+click selects the inclusive forward range and keeps the anchor", () => {
    const r = nextSelection(["/b.jpg"], "/b.jpg", "/d.jpg", SHIFT, ORDER);
    expect(r.selectedPaths).toEqual(["/b.jpg", "/c.jpg", "/d.jpg"]);
    expect(r.anchorPath).toBe("/b.jpg"); // anchor unchanged for re-ranging
  });

  it("Shift+click ranges correctly when the click is ABOVE the anchor (reverse)", () => {
    const r = nextSelection(["/d.jpg"], "/d.jpg", "/b.jpg", SHIFT, ORDER);
    expect(r.selectedPaths).toEqual(["/b.jpg", "/c.jpg", "/d.jpg"]);
    expect(r.anchorPath).toBe("/d.jpg");
  });

  it("Shift+click with NO anchor falls back to a plain click", () => {
    const r = nextSelection([], null, "/c.jpg", SHIFT, ORDER);
    expect(r.selectedPaths).toEqual(["/c.jpg"]);
    expect(r.anchorPath).toBe("/c.jpg");
  });

  it("Shift+click whose anchor scrolled out of the visible order falls back to plain", () => {
    const r = nextSelection(["/x.jpg"], "/x.jpg", "/c.jpg", SHIFT, ORDER);
    expect(r.selectedPaths).toEqual(["/c.jpg"]);
    expect(r.anchorPath).toBe("/c.jpg");
  });

  it("successive Shift+clicks re-range from the stable anchor (not the last click)", () => {
    const first = nextSelection(["/b.jpg"], "/b.jpg", "/d.jpg", SHIFT, ORDER);
    const second = nextSelection(
      first.selectedPaths,
      first.anchorPath,
      "/a.jpg",
      SHIFT,
      ORDER
    );
    expect(second.selectedPaths).toEqual(["/a.jpg", "/b.jpg"]); // a..b, not d..a
    expect(second.anchorPath).toBe("/b.jpg");
  });
});
