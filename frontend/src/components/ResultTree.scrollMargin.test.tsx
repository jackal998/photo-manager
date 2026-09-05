// #699 — the result tree's virtualizer coordinate space must match the scroll
// container's own.
//
// The sticky ColumnHeaderRow is a normal-flow sibling ABOVE the virtualizer's
// spacer inside the same scroll container, so the row list starts one
// header-height into the container's content. The virtualizer compares
// `scrollTop` (measured from the content top, header included) against row
// coordinates, so it must be told about that offset via `scrollMargin` — and
// the rows, which live inside the spacer (already below the header), must
// subtract it exactly once when positioning themselves.
//
// Two opposite regressions are pinned here, because either one is invisible on
// screen while `overscan: 10` buffers the windowing error:
//   * scrollMargin dropped / hardcoded to 0 → windowing is a header-height off
//     (the original #699 bug: blank gap / late row mount near scroll edges once
//     overscan shrinks, and scrollToIndex lands off-centre).
//   * scrollMargin added but not subtracted in the row transform → every row
//     renders a header-height too low (a double offset), which shifts the whole
//     list down and pushes the last row past the spacer's bottom.
//
// jsdom returns zeroes from every layout API, so the header/row boxes are
// stubbed; without a stub the measured header height is 0 and both regressions
// look identical to a correct build.

import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// The rendered output is IDENTICAL with and without the fix (start grows by the
// margin, the transform subtracts it again), which is exactly why the bug hid
// behind `overscan` for so long. So the option the virtualizer is actually
// configured with has to be observed directly — the real hook still runs, its
// argument is merely recorded on the way in.
const captured = vi.hoisted(() => ({ options: [] as Record<string, unknown>[] }));
vi.mock("@tanstack/react-virtual", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-virtual")>();
  return {
    ...actual,
    useVirtualizer: (options: Parameters<typeof actual.useVirtualizer>[0]) => {
      captured.options.push(options as unknown as Record<string, unknown>);
      return actual.useVirtualizer(options);
    },
  };
});

import { ResultTree } from "./ResultTree";
import { useAppStore } from "@/store/useAppStore";
import { MAIN_RESULT_TREE, RESULT_COL_HEADER_ROW } from "@/testids";
import { DEFAULT_COLUMN_WIDTHS } from "@/lib/resultColumns";
import { DEFAULT_PANEL_WIDTHS } from "@/lib/panelWidths";
import type { FileRow as FileRowData, Group } from "@/api/types";

const HEADER_H = 28; // the real sticky header measures ~28px in the browser
const ROW_H = 50; // uniform stubbed row box, so starts are 0/50/100/…

function mkRow(basename: string): FileRowData {
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
    file_size_bytes: 1000,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: `/api/image?path=/photos/${basename}&size=512`,
  } as FileRowData;
}

// One group → vrow 0 is the group header, vrows 1..3 are the file rows.
const GROUPS: Group[] = [
  {
    group_number: 1,
    member_count: 3,
    items: [mkRow("a.jpg"), mkRow("b.jpg"), mkRow("c.jpg")],
  },
];

function seed() {
  useAppStore.setState({
    manifest: {
      path: "/manifests/test.db",
      groups: GROUPS,
      totalGroups: 1,
      totalFiles: 3,
      loading: false,
      error: null,
    },
    resultView: {
      sortColumn: null,
      sortDirection: "asc",
      columnWidths: { ...DEFAULT_COLUMN_WIDTHS },
      panelWidths: { ...DEFAULT_PANEL_WIDTHS },
    },
  });
}

// Virtualizer needs non-zero offset dims in jsdom (same stub as the sibling
// ResultTree suites) — it reads the scroll container through offsetWidth/Height
// and measures each rendered row the same way. Rows report ROW_H so their
// `start` coordinates are 0/ROW_H/2*ROW_H apart from the margin; everything
// else reports a tall viewport so the whole fixture is inside the window.
function stubOffsetDims(height = 4000, width = 1024) {
  const h = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
  const w = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get(this: HTMLElement) {
      return this.hasAttribute("data-index") ? ROW_H : height;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => width,
  });
  return () => {
    if (h) Object.defineProperty(HTMLElement.prototype, "offsetHeight", h);
    if (w) Object.defineProperty(HTMLElement.prototype, "offsetWidth", w);
  };
}

/** Give the header and the virtual rows real boxes; everything else stays 0. */
function stubRects(state: { headerHeight: number }) {
  const original = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function (this: Element): DOMRect {
    const el = this as HTMLElement;
    let height = 0;
    if (el.dataset?.testid === RESULT_COL_HEADER_ROW) {
      height = state.headerHeight;
    } else if (el.hasAttribute("data-index")) {
      height = ROW_H;
    }
    return {
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 1024,
      bottom: height,
      width: 1024,
      height,
      toJSON: () => ({}),
    } as DOMRect;
  };
  return () => {
    Element.prototype.getBoundingClientRect = original;
  };
}

/** Minimal ResizeObserver so the live-remeasure path can be driven.
 *
 *  The virtualizer registers observers of its own once this global exists, so
 *  the callbacks are invoked with an empty entry list — theirs iterate it and
 *  no-op, ours ignores it and re-reads the header box. */
function stubResizeObserver(): { fire: () => void; restore: () => void } {
  type ROCallback = (entries: ResizeObserverEntry[]) => void;
  const callbacks: ROCallback[] = [];
  const original = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
  class FakeResizeObserver {
    constructor(cb: ROCallback) {
      callbacks.push(cb);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
  return {
    fire: () => callbacks.forEach((cb) => cb([])),
    restore: () => {
      (globalThis as { ResizeObserver?: unknown }).ResizeObserver = original;
    },
  };
}

/** translateY(...) px value of the wrapper carrying `data-index={index}`. */
function rowTranslateY(index: number): number {
  const el = document.querySelector(`[data-index="${index}"]`);
  if (el === null) throw new Error(`no virtual row rendered at index ${index}`);
  const transform = (el as HTMLElement).style.transform;
  const m = /translateY\((-?[\d.]+)px\)/.exec(transform);
  if (m === null) throw new Error(`row ${index} has no translateY: ${transform}`);
  return Number(m[1]);
}

describe("ResultTree virtualizer scrollMargin (#699)", () => {
  let restoreDims: (() => void) | undefined;
  let restoreRects: (() => void) | undefined;
  const rectState = { headerHeight: HEADER_H };

  /** The scrollMargin the LAST render handed to useVirtualizer. */
  function configuredScrollMargin(): unknown {
    const last = captured.options.at(-1);
    if (last === undefined) throw new Error("useVirtualizer was never called");
    return last.scrollMargin;
  }

  beforeEach(() => {
    localStorage.clear();
    captured.options.length = 0;
    rectState.headerHeight = HEADER_H;
    restoreDims = stubOffsetDims();
    restoreRects = stubRects(rectState);
    seed();
  });

  afterEach(() => {
    restoreRects?.();
    restoreDims?.();
  });

  it("hands the virtualizer the sticky header's measured height", () => {
    render(<ResultTree />);

    const header = screen.getByTestId(RESULT_COL_HEADER_ROW);
    expect(header.getBoundingClientRect().height).toBe(HEADER_H);
    // Without this the windowing math is a header-height off (the #699 bug).
    expect(configuredScrollMargin()).toBe(HEADER_H);
    // Same value mirrored onto the tree root: s47 adds it to each row's
    // translateY to recover the virtualizer's own coordinate and compares that
    // with where the row really sits.
    expect(screen.getByTestId(MAIN_RESULT_TREE)).toHaveAttribute(
      "data-scroll-margin",
      String(HEADER_H)
    );
  });

  it("positions rows at start - scrollMargin, so nothing double-offsets", () => {
    render(<ResultTree />);

    // With scrollMargin applied, item starts are HEADER_H, HEADER_H + ROW_H, …
    // and the spacer already begins at HEADER_H — so the first row sits at 0
    // inside it and each later row one ROW_H further down. A build that forgot
    // to subtract the margin would report HEADER_H and HEADER_H + 2 * ROW_H.
    expect(rowTranslateY(0)).toBe(0);
    expect(rowTranslateY(2)).toBe(2 * ROW_H);
  });

  it("re-measures when the header's height changes", () => {
    const ro = stubResizeObserver();
    try {
      render(<ResultTree />);
      expect(screen.getByTestId(MAIN_RESULT_TREE)).toHaveAttribute(
        "data-scroll-margin",
        String(HEADER_H)
      );

      // A taller header (a wrapped label after a language switch, a late font
      // load) moves the list origin down; a mount-only measurement would leave
      // the virtualizer's coordinates stale by the difference.
      rectState.headerHeight = HEADER_H + 12;
      act(() => ro.fire());

      expect(configuredScrollMargin()).toBe(HEADER_H + 12);
      expect(screen.getByTestId(MAIN_RESULT_TREE)).toHaveAttribute(
        "data-scroll-margin",
        String(HEADER_H + 12)
      );
      expect(rowTranslateY(0)).toBe(0);
    } finally {
      ro.restore();
    }
  });
});
