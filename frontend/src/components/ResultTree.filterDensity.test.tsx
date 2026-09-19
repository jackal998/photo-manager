// ResultTree's two slice-TB couplings (#878): the toolbar filter narrowing the
// list, and the density switch forcing the virtualiser to re-measure.
//
// The second one is the reason this file exists. `estimateSize` is the
// virtualiser's contract, and @tanstack/react-virtual caches a MEASURED size
// per index — so changing density re-renders every row at its new height while
// the cached 72px offsets stay, and the list's total height and every scroll
// target past the first screen are wrong. Nothing goes red: `overscan: 10`
// buffers the windowing error, which is exactly what
// ResultTree.scrollMargin.test.tsx's header says about the #699 class. The only
// way to pin it is to observe the `measure()` call itself.

import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// Wrap the REAL hook, recording the virtualizer it returns, so `measure` can be
// observed without replacing the windowing logic the rest of the tree depends
// on (the same "record on the way through" shape scrollMargin.test.tsx uses).
const captured = vi.hoisted(() => ({
  measureCalls: 0,
  reset() {
    this.measureCalls = 0;
  },
}));
vi.mock("@tanstack/react-virtual", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@tanstack/react-virtual")>();
  // `useVirtualizer` returns the SAME instance object on every render, so the
  // patch has to be applied once per instance. Re-wrapping each render nests
  // the counters inside each other and turns ONE call into N — which is what
  // this file first measured (3 for a single density change).
  const patched = new WeakSet<object>();
  return {
    ...actual,
    useVirtualizer: (options: Parameters<typeof actual.useVirtualizer>[0]) => {
      const v = actual.useVirtualizer(options);
      if (!patched.has(v)) {
        patched.add(v);
        const realMeasure = v.measure.bind(v);
        v.measure = () => {
          captured.measureCalls += 1;
          realMeasure();
        };
      }
      return v;
    },
  };
});

import { ResultTree } from "./ResultTree";
import { useAppStore } from "@/store/useAppStore";
import { useI18nStore } from "@/i18n/useI18nStore";
import { DEFAULT_COLUMN_WIDTHS } from "@/lib/resultColumns";
import { DEFAULT_PANEL_WIDTHS } from "@/lib/panelWidths";
import { rowFileTestid, rowGroupTestid } from "@/testids";
import type { FileRow as FileRowData, Group } from "@/api/types";

function mkRow(folder: string, basename: string): FileRowData {
  return {
    file_path: `${folder}/${basename}`,
    basename,
    folder,
    action: "REVIEW_DUPLICATE",
    user_decision: "",
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "near_dup", percent: 98 },
    score: null,
    file_size_bytes: 1024,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: `/api/image?path=${encodeURIComponent(`${folder}/${basename}`)}&size=128`,
  } as FileRowData;
}

const GROUPS: Group[] = [
  {
    group_number: 1,
    member_count: 2,
    items: [
      mkRow("/photos/Taiwan", "neardup_00_q95.jpg"),
      mkRow("/photos/Taiwan", "neardup_01_q80.jpg"),
    ],
  },
  {
    group_number: 2,
    member_count: 1,
    items: [mkRow("/photos/Japan", "kyoto.jpg")],
  },
];

function stubOffsetDims() {
  const h = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
  const w = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => 4000,
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => 1280,
  });
  return () => {
    if (h) Object.defineProperty(HTMLElement.prototype, "offsetHeight", h);
    if (w) Object.defineProperty(HTMLElement.prototype, "offsetWidth", w);
  };
}

function seed(filterText = "") {
  act(() => {
    useAppStore.setState({
      manifest: {
        path: "/m/test.db",
        groups: GROUPS,
        totalGroups: 2,
        totalFiles: 3,
        loading: false,
        error: null,
      },
      selection: { selectedPaths: [], anchorPath: null, scrollToPath: null },
      resultView: {
        sortColumn: null,
        sortDirection: "asc",
        columnWidths: { ...DEFAULT_COLUMN_WIDTHS },
        panelWidths: { ...DEFAULT_PANEL_WIDTHS },
        density: "comfortable",
        filterText,
      },
    });
  });
}

describe("ResultTree × toolbar filter (slice TB)", () => {
  let restore: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    useI18nStore.setState({ locale: "en", catalog: {} });
    restore = stubOffsetDims();
    captured.reset();
  });
  afterEach(() => restore?.());

  it("renders every group and row with an empty filter", () => {
    seed("");
    render(<ResultTree />);
    expect(screen.getByTestId(rowGroupTestid("1"))).toBeInTheDocument();
    expect(screen.getByTestId(rowGroupTestid("2"))).toBeInTheDocument();
    expect(
      screen.getByTestId(rowFileTestid("1", "neardup_00_q95.jpg"))
    ).toBeInTheDocument();
  });

  it("keeps only the matching row and hides the group that has none", () => {
    seed("q95");
    render(<ResultTree />);
    expect(
      screen.getByTestId(rowFileTestid("1", "neardup_00_q95.jpg"))
    ).toBeInTheDocument();
    expect(screen.queryByTestId(rowFileTestid("1", "neardup_01_q80.jpg"))).toBeNull();
    // Group 2's only row does not match, so the header must go too — an empty
    // header is a promise of rows that are not there.
    expect(screen.queryByTestId(rowGroupTestid("2"))).toBeNull();
    expect(screen.getByTestId(rowGroupTestid("1"))).toBeInTheDocument();
  });

  it("matches on the FOLDER, not just the filename", () => {
    seed("japan");
    render(<ResultTree />);
    expect(screen.getByTestId(rowFileTestid("2", "kyoto.jpg"))).toBeInTheDocument();
    expect(screen.queryByTestId(rowGroupTestid("1"))).toBeNull();
  });

  it("restores every row when the filter is cleared", () => {
    seed("q95");
    const view = render(<ResultTree />);
    expect(screen.queryByTestId(rowGroupTestid("2"))).toBeNull();

    act(() => useAppStore.getState().setFilterText(""));
    view.rerender(<ResultTree />);

    expect(screen.getByTestId(rowGroupTestid("2"))).toBeInTheDocument();
    expect(
      screen.getByTestId(rowFileTestid("1", "neardup_01_q80.jpg"))
    ).toBeInTheDocument();
  });

  it("does not touch decisions — the filter is a view control", () => {
    seed("");
    const before = JSON.stringify(useAppStore.getState().manifest.groups);
    render(<ResultTree />);
    act(() => useAppStore.getState().setFilterText("q95"));
    expect(JSON.stringify(useAppStore.getState().manifest.groups)).toBe(before);
  });
});

describe("ResultTree × density switch (slice TB)", () => {
  let restore: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    useI18nStore.setState({ locale: "en", catalog: {} });
    restore = stubOffsetDims();
    captured.reset();
  });
  afterEach(() => restore?.());

  it("does NOT re-measure on mount — that would discard the first layout pass", () => {
    // `measure()` clears the cache. Calling it unconditionally leaves every row
    // on its estimate instead of its measured size, which is what turned
    // scrollMargin.test.tsx and keyboard.test.tsx red while this was being
    // written (rows landed 16px out).
    seed("");
    render(<ResultTree />);
    expect(captured.measureCalls).toBe(0);
  });

  it("re-measures once when the density preference changes", () => {
    seed("");
    const view = render(<ResultTree />);
    expect(captured.measureCalls).toBe(0);

    act(() => useAppStore.getState().setDensity("compact"));
    view.rerender(<ResultTree />);
    expect(captured.measureCalls).toBe(1);

    // A re-render that does NOT change the density must not re-measure again,
    // or every unrelated store write would throw the row sizes away.
    act(() => useAppStore.getState().setFilterText("q95"));
    view.rerender(<ResultTree />);
    expect(captured.measureCalls).toBe(1);

    act(() => useAppStore.getState().setDensity("comfortable"));
    view.rerender(<ResultTree />);
    expect(captured.measureCalls).toBe(2);
  });
});
