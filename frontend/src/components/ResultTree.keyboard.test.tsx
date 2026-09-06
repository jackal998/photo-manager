// #709 — roving arrow-key cursor through the virtualized result tree.
//
// The desktop pins this in qa/scenarios/s26_keyboard_navigation.py (steps 1/3):
// the tree takes keyboard focus and Down/Up walk the visible rows, with the
// selected row surviving a model rebuild. The web tree's rows are absolutely
// positioned, virtualized, non-focusable <div>s, so the cursor is an
// aria-activedescendant on the scroll container rather than DOM focus per row.
//
// What each test here catches, in user terms:
//   * the arrows do nothing / move by the wrong step / skip the group header
//     (the visible order and the keyboard order must be the same order);
//   * the arrows update only a highlight and not the store, so the preview pane
//     and the d/k decision shortcuts (#708) act on a different row than the one
//     the user is looking at;
//   * arrowing onto a group header silently collapses it;
//   * the cursor is stored as an INDEX, so the first decision write (which
//     rebuilds manifest.groups) drops it or moves it to a different row;
//   * the handler is moved to `document` (the sibling d/k pattern), so pressing
//     Down while typing in the manifest-path field jumps the tree;
//   * the active row is scrolled to the container's top edge and lands UNDER
//     the sticky column header (#699/#846) — which is exactly what this
//     component does the moment `scrollPaddingStart` is dropped.
//
// jsdom has no layout, so the geometry test stubs exactly the boxes the
// virtualizer reads (offsetHeight, scrollHeight/clientHeight, scrollTop) — the
// same technique as ResultTree.scrollMargin.test.tsx.

import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// The virtualizer instance is the authority on where a scroll would land, so
// the real hook runs and both its options and its return value are recorded on
// the way through.
const captured = vi.hoisted(() => ({
  options: [] as Record<string, unknown>[],
  instances: [] as ReturnType<
    typeof import("@tanstack/react-virtual").useVirtualizer
  >[],
}));
vi.mock("@tanstack/react-virtual", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-virtual")>();
  return {
    ...actual,
    useVirtualizer: (options: Parameters<typeof actual.useVirtualizer>[0]) => {
      captured.options.push(options as unknown as Record<string, unknown>);
      const instance = actual.useVirtualizer(options);
      captured.instances.push(instance);
      return instance;
    },
  };
});

import { ResultTree } from "./ResultTree";
import { useDecisionShortcuts } from "@/hooks/useDecisionShortcuts";
import { useAppStore } from "@/store/useAppStore";
import {
  MAIN_RESULT_TREE,
  RESULT_COL_HEADER_ROW,
  rowFileTestid,
  rowGroupTestid,
} from "@/testids";
import { DEFAULT_COLUMN_WIDTHS } from "@/lib/resultColumns";
import { DEFAULT_PANEL_WIDTHS } from "@/lib/panelWidths";
import type { FileRow as FileRowData, Group } from "@/api/types";

const HEADER_H = 28; // the sticky ColumnHeaderRow measures ~28px in a browser
const ROW_H = 50; // uniform stubbed row box

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

function mkGroup(groupNumber: number, basenames: string[]): Group {
  return {
    group_number: groupNumber,
    member_count: basenames.length,
    items: basenames.map(mkRow),
  };
}

/** Two two-member groups. Visible row order (vrow index → row):
 *  0 header g1 · 1 a1 · 2 a2 · 3 header g2 · 4 b1 · 5 b2 */
const TWO_GROUPS: Group[] = [
  mkGroup(1, ["a1.jpg", "a2.jpg"]),
  mkGroup(2, ["b1.jpg", "b2.jpg"]),
];

function seed(groups: Group[]): void {
  useAppStore.setState({
    manifest: {
      path: "/manifests/test.db",
      groups,
      totalGroups: groups.length,
      totalFiles: groups.reduce((n, g) => n + g.items.length, 0),
      loading: false,
      error: null,
    },
    selection: { selectedPaths: [], anchorPath: null, scrollToPath: null },
    preview: { selectedFilePath: null, fullResPath: null, selectedGroupId: null },
    resultView: {
      sortColumn: null,
      sortDirection: "asc",
      columnWidths: { ...DEFAULT_COLUMN_WIDTHS },
      panelWidths: { ...DEFAULT_PANEL_WIDTHS },
    },
  });
}

/** The virtualizer reads the scroll container through offsetHeight and each
 *  rendered row the same way; jsdom returns 0 for both. */
function stubOffsetDims(viewportHeight: number) {
  const h = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetHeight"
  );
  const w = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get(this: HTMLElement) {
      return this.hasAttribute("data-index") ? ROW_H : viewportHeight;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => 1024,
  });
  return () => {
    if (h) Object.defineProperty(HTMLElement.prototype, "offsetHeight", h);
    if (w) Object.defineProperty(HTMLElement.prototype, "offsetWidth", w);
  };
}

/** Only the sticky header needs a real box: it is what the component measures
 *  into `scrollMargin` (and therefore into `scrollPaddingStart`). */
function stubHeaderRect() {
  const original = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function (this: Element): DOMRect {
    const el = this as HTMLElement;
    const height = el.dataset?.testid === RESULT_COL_HEADER_ROW ? HEADER_H : 0;
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

function tree(): HTMLElement {
  return screen.getByTestId(MAIN_RESULT_TREE);
}

/** The id `aria-activedescendant` names, resolved to the element carrying it. */
function activeRowElement(): HTMLElement {
  const id = tree().getAttribute("aria-activedescendant");
  expect(id).toBeTruthy();
  const el = document.getElementById(id as string);
  if (el === null) {
    throw new Error(`aria-activedescendant points at absent id ${id}`);
  }
  return el;
}

/** Press a bare arrow on the tree container; returns whether the component
 *  called preventDefault (it must, or the container also scrolls natively). */
function arrow(key: "ArrowDown" | "ArrowUp"): boolean {
  let prevented = false;
  act(() => {
    prevented = !fireEvent.keyDown(tree(), { key });
  });
  return prevented;
}

function selectedPaths(): string[] {
  return useAppStore.getState().selection.selectedPaths;
}

describe("ResultTree roving arrow-key cursor (#709)", () => {
  let restoreDims: (() => void) | undefined;
  let restoreRects: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    captured.options.length = 0;
    captured.instances.length = 0;
    restoreDims = stubOffsetDims(4000);
    restoreRects = stubHeaderRect();
    seed(TWO_GROUPS);
  });

  afterEach(() => {
    restoreRects?.();
    restoreDims?.();
  });

  it("ArrowDown walks the visible row order, group headers included", () => {
    render(<ResultTree />);
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a1.jpg")));
    expect(selectedPaths()).toEqual(["/photos/a1.jpg"]);

    expect(arrow("ArrowDown")).toBe(true);
    // The next VISIBLE row is the second file of group 1.
    expect(selectedPaths()).toEqual(["/photos/a2.jpg"]);
    expect(screen.getByTestId(rowFileTestid("1", "a2.jpg"))).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(screen.getByTestId(rowFileTestid("1", "a1.jpg"))).toHaveAttribute(
      "aria-selected",
      "false"
    );
    // …and the preview pane follows the cursor, like a click.
    expect(useAppStore.getState().preview.selectedFilePath).toBe(
      "/photos/a2.jpg"
    );

    // Past the last file of a group the next visible row is the NEXT GROUP's
    // header — the same order the tree renders.
    arrow("ArrowDown");
    expect(activeRowElement()).toContainElement(
      screen.getByTestId(rowGroupTestid("2"))
    );
    expect(useAppStore.getState().preview.selectedGroupId).toBe(2);

    arrow("ArrowDown");
    expect(selectedPaths()).toEqual(["/photos/b1.jpg"]);
  });

  it("ArrowUp walks back through the same order and clamps at the first row", () => {
    render(<ResultTree />);
    fireEvent.click(screen.getByTestId(rowFileTestid("2", "b1.jpg")));

    arrow("ArrowUp"); // → group 2 header
    expect(activeRowElement()).toContainElement(
      screen.getByTestId(rowGroupTestid("2"))
    );
    arrow("ArrowUp"); // → a2
    expect(selectedPaths()).toEqual(["/photos/a2.jpg"]);
    arrow("ArrowUp"); // → a1
    expect(selectedPaths()).toEqual(["/photos/a1.jpg"]);
    arrow("ArrowUp"); // → group 1 header (the first visible row)
    expect(activeRowElement()).toContainElement(
      screen.getByTestId(rowGroupTestid("1"))
    );

    // One more press must STAY there: wrapping to the bottom would silently
    // teleport the reviewer to the end of a 5000-row manifest.
    arrow("ArrowUp");
    expect(activeRowElement()).toContainElement(
      screen.getByTestId(rowGroupTestid("1"))
    );
  });

  it("arrowing onto a group header selects the group without collapsing it", () => {
    render(<ResultTree />);
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a2.jpg")));

    arrow("ArrowDown"); // → group 2 header

    const header = screen.getByTestId(rowGroupTestid("2"));
    expect(header).toHaveAttribute("aria-expanded", "true");
    // Its member rows are still rendered — the cursor must not toggle collapse.
    expect(screen.getByTestId(rowFileTestid("2", "b1.jpg"))).toBeInTheDocument();
    expect(useAppStore.getState().preview.selectedGroupId).toBe(2);
    // A header is not part of the multi-selection model (docs/features.md:782),
    // and Qt's set_decision_to_highlighted filters type=="file" out of the
    // CURRENT selection — so moving onto a header CLEARS the file selection.
    // Leaving a2 selected here is what let `d` write a decision to a row the
    // cursor had visibly left (#849 review, finding 2).
    expect(selectedPaths()).toEqual([]);
    expect(useAppStore.getState().preview.selectedFilePath).toBeNull();
  });

  it("makes d/k no-ops while the cursor sits on a group header", () => {
    // The decision shortcuts act on selection.selectedPaths, so this is the
    // user-visible half of the rule above: with the cursor on a header there is
    // nothing selected and `d` must not stage a delete anywhere.
    const originalSetDecisions = useAppStore.getState().setDecisions;
    const decideSpy = vi.fn();
    useAppStore.setState({
      setDecisions: decideSpy as unknown as typeof originalSetDecisions,
    });
    try {
      render(<ResultTree />);
      renderHook(() => useDecisionShortcuts());

      fireEvent.click(screen.getByTestId(rowFileTestid("1", "a2.jpg")));
      arrow("ArrowDown"); // → group 2 header
      act(() => {
        document.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "d",
            bubbles: true,
            cancelable: true,
          })
        );
      });

      expect(decideSpy).not.toHaveBeenCalled();

      // Control: arrowing back onto a file row re-selects it, so `d` works
      // again — the rule above must not have disabled the shortcut outright.
      arrow("ArrowDown"); // → b1
      expect(selectedPaths()).toEqual(["/photos/b1.jpg"]);
      act(() => {
        document.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "d",
            bubbles: true,
            cancelable: true,
          })
        );
      });
      expect(decideSpy).toHaveBeenCalledWith(["/photos/b1.jpg"], "delete");
    } finally {
      useAppStore.setState({ setDecisions: originalSetDecisions });
    }
  });

  it("moves the cursor to a right-clicked row", () => {
    // App resets the selection to the right-clicked row when it is outside the
    // current selection (App.tsx:109-120); if the cursor did not follow, the
    // next ArrowDown would continue from the last LEFT-clicked row and scroll
    // the tree back there (#849 review, finding 1).
    render(<ResultTree onContextMenu={() => {}} />);
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a1.jpg")));

    fireEvent.contextMenu(screen.getByTestId(rowFileTestid("2", "b1.jpg")));
    arrow("ArrowDown");

    expect(selectedPaths()).toEqual(["/photos/b2.jpg"]);
    expect(activeRowElement()).toContainElement(
      screen.getByTestId(rowFileTestid("2", "b2.jpg"))
    );
  });

  it("exposes each treeitem's own selection / expansion state", () => {
    render(<ResultTree />);
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a1.jpg")));

    const wrapperOf = (testid: string): HTMLElement => {
      const el = screen.getByTestId(testid).closest("[role='treeitem']");
      if (el === null) throw new Error(`${testid} has no treeitem wrapper`);
      return el as HTMLElement;
    };

    // aria-selected lives on the FileRow div and aria-expanded on the GroupRow
    // button — both CHILDREN of the treeitem, so without this the treeitem
    // announces no state at all.
    expect(wrapperOf(rowFileTestid("1", "a1.jpg"))).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(wrapperOf(rowFileTestid("1", "a2.jpg"))).toHaveAttribute(
      "aria-selected",
      "false"
    );
    expect(wrapperOf(rowGroupTestid("1"))).toHaveAttribute(
      "aria-expanded",
      "true"
    );

    // Collapsing the group must flip it on the wrapper too.
    fireEvent.click(screen.getByTestId(rowGroupTestid("1")));
    expect(wrapperOf(rowGroupTestid("1"))).toHaveAttribute(
      "aria-expanded",
      "false"
    );
  });

  it("resumes from the post-scan keeper the app scrolled to", () => {
    // loadManifest({ selectKeepers }) selects the KEEP rows and arms
    // selection.scrollToPath at the first one. Without seeding the cursor there
    // the first ArrowDown activates vrow 0 and scrolls back to the top of the
    // manifest, undoing the scan's own scroll (#849 review, finding 5).
    useAppStore.setState((s) => ({
      selection: {
        ...s.selection,
        selectedPaths: ["/photos/a2.jpg"],
        anchorPath: "/photos/a2.jpg",
        scrollToPath: "/photos/a2.jpg",
      },
    }));
    render(<ResultTree />);
    // The one-shot signal is still consumed exactly once.
    expect(useAppStore.getState().selection.scrollToPath).toBeNull();

    arrow("ArrowDown");
    expect(activeRowElement()).toContainElement(
      screen.getByTestId(rowGroupTestid("2"))
    );
  });

  it("names the active row through aria-activedescendant on a focusable tree", () => {
    render(<ResultTree />);
    const container = tree();
    // Focus lives on the container, never on a row: a virtualized row can be
    // unmounted while it is still the cursor.
    expect(container).toHaveAttribute("tabindex", "0");
    expect(container).toHaveAttribute("role", "tree");
    expect(container).not.toHaveAttribute("aria-activedescendant");

    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a1.jpg")));
    arrow("ArrowDown");

    const active = activeRowElement();
    expect(active).toHaveAttribute("role", "treeitem");
    expect(active).toContainElement(
      screen.getByTestId(rowFileTestid("1", "a2.jpg"))
    );
  });

  it("ignores modifier-bearing arrows (Qt NoModifier mirror)", () => {
    render(<ResultTree />);
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a1.jpg")));

    for (const mods of [
      { shiftKey: true },
      { ctrlKey: true },
      { altKey: true },
      { metaKey: true },
    ]) {
      act(() => {
        fireEvent.keyDown(tree(), { key: "ArrowDown", ...mods });
      });
      expect(selectedPaths()).toEqual(["/photos/a1.jpg"]);
    }
  });

  it("arrows typed outside the tree never move the cursor", () => {
    // The d/k shortcuts listen on `document` by necessity; if this handler ever
    // moves there too, pressing Down in the manifest-path input would jump the
    // tree. Container scoping is what prevents that, so it is pinned here.
    render(
      <>
        <input data-testid="outside-input" />
        <ResultTree />
      </>
    );
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a1.jpg")));
    const before = tree().getAttribute("aria-activedescendant");

    act(() => {
      fireEvent.keyDown(screen.getByTestId("outside-input"), {
        key: "ArrowDown",
      });
      fireEvent.keyDown(document.body, { key: "ArrowDown" });
    });

    expect(selectedPaths()).toEqual(["/photos/a1.jpg"]);
    expect(tree().getAttribute("aria-activedescendant")).toBe(before);
  });

  it("keeps the cursor on the same row across a manifest rebuild", () => {
    render(<ResultTree />);
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "a1.jpg")));
    arrow("ArrowDown");
    const activeBefore = tree().getAttribute("aria-activedescendant");

    // What a decision write produces: a brand-new groups array holding
    // brand-new row objects for the same paths. A cursor held as an index into
    // the old vrows would survive this by luck; one held as a row identity is
    // what actually keeps the reviewer's place (Qt s26 step 3).
    act(() => {
      useAppStore.setState((s) => ({
        manifest: {
          ...s.manifest,
          groups: [
            mkGroup(1, ["a1.jpg", "a2.jpg"]),
            mkGroup(2, ["b1.jpg", "b2.jpg"]),
          ],
        },
      }));
    });

    expect(tree().getAttribute("aria-activedescendant")).toBe(activeBefore);
    expect(screen.getByTestId(rowFileTestid("1", "a2.jpg"))).toHaveAttribute(
      "aria-selected",
      "true"
    );
    // The cursor is still usable, not merely still displayed.
    arrow("ArrowDown");
    expect(activeRowElement()).toContainElement(
      screen.getByTestId(rowGroupTestid("2"))
    );
  });
});

// ---------------------------------------------------------------------------
// Scroll-into-view geometry — the active row must clear the sticky header
// ---------------------------------------------------------------------------

describe("ResultTree arrow-key scroll-into-view (#709 over #699/#846)", () => {
  const VIEWPORT_H = 300;
  const FILES = 12;
  // vrow 0 is the group header, vrows 1..12 the files; every rendered row
  // measures ROW_H, and the list starts HEADER_H into the container's content.
  const TOTAL_H = HEADER_H + (FILES + 1) * ROW_H;
  const START_OF = (index: number) => HEADER_H + index * ROW_H;
  const SCROLL_TOP = 300;

  let restoreDims: (() => void) | undefined;
  let restoreRects: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    captured.options.length = 0;
    captured.instances.length = 0;
    restoreDims = stubOffsetDims(VIEWPORT_H);
    restoreRects = stubHeaderRect();
    seed([
      mkGroup(
        1,
        Array.from({ length: FILES }, (_unused, i) => `f${String(i).padStart(2, "0")}.jpg`)
      ),
    ]);
  });

  afterEach(() => {
    restoreRects?.();
    restoreDims?.();
  });

  it("hands the virtualizer scrollPaddingStart equal to the sticky header height", () => {
    render(<ResultTree />);
    expect(captured.options.at(-1)?.scrollPaddingStart).toBe(HEADER_H);
    // Same number the #699 windowing offset uses — they are the same header.
    expect(captured.options.at(-1)?.scrollMargin).toBe(HEADER_H);
  });

  it("never leaves aria-activedescendant naming an unmounted row", () => {
    // The cursor deliberately survives its row being virtualized away — that is
    // why activedescendant was chosen over roving tabindex. But an IDREF that
    // resolves to nothing is invalid ARIA and announces nothing, so while the
    // active row is outside the mounted window the attribute must be absent
    // rather than dangling (#849 review, finding 3).
    seed([
      mkGroup(
        1,
        Array.from({ length: 60 }, (_unused, i) => `t${String(i).padStart(2, "0")}.jpg`)
      ),
    ]);
    render(<ResultTree />);
    const container = tree();

    fireEvent.click(screen.getByTestId(rowFileTestid("1", "t00.jpg")));
    // Mounted: the attribute is present and resolves.
    const idNear = container.getAttribute("aria-activedescendant");
    expect(idNear).toBeTruthy();
    expect(document.getElementById(idNear as string)).not.toBeNull();

    // Scroll ~50 rows away — far past `overscan: 10`, so row 1 unmounts.
    Object.defineProperty(container, "scrollHeight", {
      configurable: true,
      value: HEADER_H + 61 * ROW_H,
    });
    Object.defineProperty(container, "clientHeight", {
      configurable: true,
      value: VIEWPORT_H,
    });
    Object.defineProperty(container, "scrollTop", {
      configurable: true,
      value: HEADER_H + 50 * ROW_H,
    });
    act(() => {
      fireEvent.scroll(container);
    });

    expect(
      document.querySelector(`[data-testid="${rowFileTestid("1", "t00.jpg")}"]`)
    ).toBeNull(); // non-vacuity: the active row really did unmount
    const idFar = container.getAttribute("aria-activedescendant");
    if (idFar !== null) {
      expect(document.getElementById(idFar)).not.toBeNull();
    }
    expect(idFar).toBeNull();
  });

  it("ArrowUp onto a row above the viewport lands it below the sticky header", () => {
    render(<ResultTree />);
    const container = tree();

    // Give the container the layout jsdom does not compute: a real scrollable
    // range (getMaxScrollOffset reads scrollHeight/clientHeight) and a scroll
    // position 300px down, which puts rows 0..5 above or under the header.
    Object.defineProperty(container, "scrollHeight", {
      configurable: true,
      value: TOTAL_H,
    });
    Object.defineProperty(container, "clientHeight", {
      configurable: true,
      value: VIEWPORT_H,
    });
    Object.defineProperty(container, "scrollTop", {
      configurable: true,
      value: SCROLL_TOP,
    });
    const scrollTo = vi.fn();
    (container as unknown as { scrollTo: unknown }).scrollTo = scrollTo;
    act(() => {
      fireEvent.scroll(container);
    });

    // Cursor onto f05 (vrow 6, the first row fully clear of the header), then
    // ArrowUp onto f04 (vrow 5) — which sits ABOVE the viewport at this scroll
    // position, so the component has to bring it into view.
    fireEvent.click(screen.getByTestId(rowFileTestid("1", "f05.jpg")));
    scrollTo.mockClear();
    arrow("ArrowUp");
    expect(screen.getByTestId(rowFileTestid("1", "f04.jpg"))).toHaveAttribute(
      "aria-selected",
      "true"
    );

    // It really scrolled — and to the right place: with scrollPaddingStart
    // deleted this same press asks for offset 278 instead of 250, which puts
    // the row's top at viewport 0, i.e. underneath the sticky header (measured
    // by mutation probe, 2026-09-06: "expected 0 to be greater than or equal
    // to 28").
    expect(scrollTo).toHaveBeenCalled();
    const target = (scrollTo.mock.calls.at(-1)?.[0] as { top: number }).top;

    // Where the row's top ends up inside the viewport, and where the sticky
    // header's bottom is (it is pinned at the container's content top and is
    // HEADER_H tall). The row must land AT or BELOW that line.
    const rowViewportTop = START_OF(5) - target;
    expect(rowViewportTop).toBeGreaterThanOrEqual(HEADER_H);
    // Exactly at the header's bottom edge — no wasted gap either.
    expect(rowViewportTop).toBe(HEADER_H);

    // Cross-check against the virtualizer's own answer for the same index, so
    // the number above is not an arithmetic coincidence of this test.
    const virtualizer = captured.instances.at(-1);
    expect(virtualizer?.getOffsetForIndex(5, "auto")?.[0]).toBe(target);
  });
});
