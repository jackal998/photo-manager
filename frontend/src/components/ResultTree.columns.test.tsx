// Integration tests for the result-tree column model (#685): header-click sort
// reorders rows per group, the default is server order (the invariant the ~21
// row-reading scenarios depend on), and sort survives an in-session manifest
// reload (the Qt s45 contract). Drives the REAL store via header clicks.

import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach } from "vitest";

import { ResultTree } from "./ResultTree";
import { useAppStore } from "@/store/useAppStore";
import { colHeaderTestid, colResizeTestid } from "@/testids";
import { DEFAULT_COLUMN_WIDTHS, columnResizeCeiling } from "@/lib/resultColumns";
import { DEFAULT_PANEL_WIDTHS } from "@/lib/panelWidths";
import type { FileRow as FileRowData, Group } from "@/api/types";

// ---------------------------------------------------------------------------
// Fixture — one group whose name-ASC order differs from its size-ASC order, so
// the two sorts are distinguishable (mirrors the q95→q65 neardup fixture).
//   server order: c (300) , a (500) , b (100)
//   name  ASC:    a , b , c
//   size  ASC:    b(100) , c(300) , a(500)
// ---------------------------------------------------------------------------

function mkRow(basename: string, size: number): FileRowData {
  return {
    file_path: `/photos/${basename}`,
    basename,
    folder: "/photos",
    action: "keep",
    user_decision: "",
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "near_dup", percent: null },
    score: null,
    file_size_bytes: size,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: `/api/image?path=/photos/${basename}&size=512`,
  } as FileRowData;
}

function makeGroups(): Group[] {
  return [
    {
      group_number: 1,
      member_count: 3,
      items: [mkRow("c.jpg", 300), mkRow("a.jpg", 500), mkRow("b.jpg", 100)],
    },
  ];
}

function seed() {
  useAppStore.setState({
    manifest: {
      path: "/manifests/test.db",
      groups: makeGroups(),
      totalGroups: 1,
      totalFiles: 3,
      loading: false,
      error: null,
    },
  });
}

function rowOrder(): string[] {
  return screen
    .getAllByTestId(/^row-file-1-/)
    .map((el) => el.getAttribute("data-testid") ?? "");
}

// Virtualizer needs non-zero offset dims in jsdom (same stub as ResultTree.test).
function stubOffsetHeight(height = 4000, width = 1024) {
  const h = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
  const w = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => height });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => width });
  return () => {
    if (h) Object.defineProperty(HTMLElement.prototype, "offsetHeight", h);
    if (w) Object.defineProperty(HTMLElement.prototype, "offsetWidth", w);
  };
}

describe("ResultTree column-model sort", () => {
  let restore: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    // Reset the column-model slice (resetStore in the sibling suite only touches
    // manifest, so sort/widths can leak between tests otherwise).
    useAppStore.setState({
      resultView: {
        sortColumn: null,
        sortDirection: "asc",
        columnWidths: { ...DEFAULT_COLUMN_WIDTHS },
        panelWidths: { ...DEFAULT_PANEL_WIDTHS },
      },
    });
    restore = stubOffsetHeight();
    seed();
  });

  afterEach(() => {
    restore?.();
  });

  it("defaults to server response order (no sort)", () => {
    render(<ResultTree />);
    expect(rowOrder()).toEqual([
      "row-file-1-c.jpg",
      "row-file-1-a.jpg",
      "row-file-1-b.jpg",
    ]);
  });

  it("clicking the File Name header sorts ascending by basename", () => {
    render(<ResultTree />);
    act(() => {
      fireEvent.click(screen.getByTestId(colHeaderTestid("name")));
    });
    expect(rowOrder()).toEqual([
      "row-file-1-a.jpg",
      "row-file-1-b.jpg",
      "row-file-1-c.jpg",
    ]);
  });

  it("a second File Name click toggles to descending", () => {
    render(<ResultTree />);
    act(() => {
      fireEvent.click(screen.getByTestId(colHeaderTestid("name")));
    });
    act(() => {
      fireEvent.click(screen.getByTestId(colHeaderTestid("name")));
    });
    expect(rowOrder()).toEqual([
      "row-file-1-c.jpg",
      "row-file-1-b.jpg",
      "row-file-1-a.jpg",
    ]);
  });

  it("clicking the Size header sorts numerically (not lexicographically)", () => {
    render(<ResultTree />);
    act(() => {
      fireEvent.click(screen.getByTestId(colHeaderTestid("size")));
    });
    // size ASC: b(100), c(300), a(500)
    expect(rowOrder()).toEqual([
      "row-file-1-b.jpg",
      "row-file-1-c.jpg",
      "row-file-1-a.jpg",
    ]);
  });

  it("preserves the sort across an in-session manifest reload (s45 contract)", () => {
    render(<ResultTree />);
    act(() => {
      fireEvent.click(screen.getByTestId(colHeaderTestid("name")));
    });
    expect(rowOrder()[0]).toBe("row-file-1-a.jpg");

    // Reopen the same manifest in-process: a fresh groups array replaces the
    // old one (what loadManifest does). The chosen sort must survive.
    act(() => {
      seed();
    });
    expect(rowOrder()).toEqual([
      "row-file-1-a.jpg",
      "row-file-1-b.jpg",
      "row-file-1-c.jpg",
    ]);
  });
});

// ---------------------------------------------------------------------------
// Shedding ↔ resize interaction (#912 round 3)
// ---------------------------------------------------------------------------
//
// The tree's available width is measured off the header's box, which jsdom
// reports as 0 — so shedding is inert here unless the box is stubbed. 986px is
// the real measurement at 1280×800 with the preview open: the full set needs
// 996px, so Resolution is shed and Shot Date is the last column that fits. That
// is exactly the state where dragging Shot Date's handle used to delete it.

/** Give every element a box `width` px wide, so the header measures as the table. */
function stubWidth(width: number) {
  const original = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function (this: Element): DOMRect {
    return {
      x: 0, y: 0, top: 0, left: 0, right: width, bottom: 28,
      width, height: 28, toJSON: () => ({}),
    } as DOMRect;
  };
  return () => {
    Element.prototype.getBoundingClientRect = original;
  };
}

function dragHandle(colId: string, byPx: number) {
  const handle = screen.getByTestId(colResizeTestid(colId));
  act(() => {
    fireEvent.mouseDown(handle, { clientX: 0 });
  });
  act(() => {
    window.dispatchEvent(new MouseEvent("mousemove", { clientX: byPx, buttons: 1 }));
  });
}

function releaseDrag() {
  act(() => {
    window.dispatchEvent(new MouseEvent("mouseup"));
  });
}

describe("ResultTree — a resize drag can never delete the column it is on (#912)", () => {
  const TABLE = 986;
  let restoreOffset: (() => void) | undefined;
  let restoreRect: (() => void) | undefined;

  function seedWidths(overrides: Partial<Record<string, number>> = {}) {
    useAppStore.setState({
      resultView: {
        sortColumn: null,
        sortDirection: "asc",
        columnWidths: { ...DEFAULT_COLUMN_WIDTHS, ...overrides },
        panelWidths: { ...DEFAULT_PANEL_WIDTHS },
      },
    });
  }

  beforeEach(() => {
    localStorage.clear();
    seedWidths();
    restoreOffset = stubOffsetHeight();
    restoreRect = stubWidth(TABLE);
    seed();
  });

  afterEach(() => {
    restoreRect?.();
    restoreOffset?.();
  });

  it("sheds Resolution but keeps Shot Date at this table width (the setup)", () => {
    render(<ResultTree />);
    expect(screen.queryByTestId(colHeaderTestid("dims"))).toBeNull();
    expect(screen.getByTestId(colHeaderTestid("date"))).toBeInTheDocument();
  });

  it("keeps Shot Date mounted through a drag well past the slack, and commits the clamp", () => {
    render(<ResultTree />);
    dragHandle("date", 200); // 112 → 312 requested, far past what the row can pay for
    // Mid-drag: the column the cursor is on must still be there. This is the
    // failure — once it unmounts, the pointer is over nothing and the window
    // listeners keep widening a column with no handle left.
    expect(screen.getByTestId(colHeaderTestid("date"))).toBeInTheDocument();
    releaseDrag();
    expect(screen.getByTestId(colHeaderTestid("date"))).toBeInTheDocument();

    const stored = useAppStore.getState().resultView.columnWidths.date;
    expect(stored).toBe(columnResizeCeiling("date", TABLE, DEFAULT_COLUMN_WIDTHS));
    expect(stored).toBeGreaterThan(DEFAULT_COLUMN_WIDTHS.date); // the drag DID widen it
    // And the number that outlives the session is the clamped one.
    expect(
      JSON.parse(localStorage.getItem("pm.result-tree.column-widths.v1") as string).date
    ).toBe(stored);
  });

  it("freezes the shed plan during a drag on a NON-sheddable column", () => {
    render(<ResultTree />);
    // Widening File Name by 300 costs more than the row has spare, so Shot Date
    // has to go — but not until the gesture is over. A column vanishing mid-drag
    // reflows every cell under the cursor the user is still dragging.
    dragHandle("name", 300);
    expect(screen.getByTestId(colHeaderTestid("date"))).toBeInTheDocument();
    releaseDrag();
    expect(screen.queryByTestId(colHeaderTestid("date"))).toBeNull();
    expect(useAppStore.getState().resultView.columnWidths.name).toBe(460);
  });

  it("self-heals a stored width that would keep a column hidden", () => {
    // What a browser holds after hitting the bug above, or after using the app
    // on a wider monitor: a Shot Date width this table cannot afford. Without
    // the heal the column never renders again at this size, and there is no
    // handle left to fix it with.
    seedWidths({ date: 400 });
    render(<ResultTree />);
    expect(screen.getByTestId(colHeaderTestid("date"))).toBeInTheDocument();
    expect(useAppStore.getState().resultView.columnWidths.date).toBe(
      DEFAULT_COLUMN_WIDTHS.date
    );
    // The correction is written through, so the bad value does not come back.
    expect(
      JSON.parse(localStorage.getItem("pm.result-tree.column-widths.v1") as string).date
    ).toBe(DEFAULT_COLUMN_WIDTHS.date);
  });
});

describe("setColumnWidth store action", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({
      resultView: {
        sortColumn: null,
        sortDirection: "asc",
        columnWidths: { ...DEFAULT_COLUMN_WIDTHS },
        panelWidths: { ...DEFAULT_PANEL_WIDTHS },
      },
    });
  });

  it("updates the width and persists it to localStorage (persist defaults true)", () => {
    act(() => {
      useAppStore.getState().setColumnWidth("name", 333);
    });
    expect(useAppStore.getState().resultView.columnWidths.name).toBe(333);
    const raw = localStorage.getItem("pm.result-tree.column-widths.v1");
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string).name).toBe(333);
  });

  it("persist=false updates in-memory only (no localStorage write — drag-move path)", () => {
    act(() => {
      useAppStore.getState().setColumnWidth("name", 250, false);
    });
    expect(useAppStore.getState().resultView.columnWidths.name).toBe(250);
    // The per-mousemove path must NOT touch localStorage; only the mouseup
    // commit (persist=true) does.
    expect(localStorage.getItem("pm.result-tree.column-widths.v1")).toBeNull();
  });

  it("clamps a sub-minimum width to MIN_COLUMN_WIDTH", () => {
    act(() => {
      useAppStore.getState().setColumnWidth("name", 5);
    });
    expect(useAppStore.getState().resultView.columnWidths.name).toBe(40);
  });
});
