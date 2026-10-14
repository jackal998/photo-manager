// Sort by decision (#923) through the real tree: the Action header is the
// canonical control, the View menu mirrors it, and — the part that protects a
// user — a decision change NEVER moves a row; a stale dot appears instead.

import { render, screen, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, afterEach } from "vitest";

import { ResultTree } from "./ResultTree";
import { MenuBar } from "./MenuBar";
import { RESORT_TRANSITION } from "./result/useResortAnimation";
import { useAppStore } from "@/store/useAppStore";
import { useI18nStore } from "@/i18n/useI18nStore";
import { DEFAULT_COLUMN_WIDTHS } from "@/lib/resultColumns";
import { DEFAULT_PANEL_WIDTHS } from "@/lib/panelWidths";
import {
  COL_SORT_STALE_DOT,
  COL_SORT_SUBLABEL,
  MENU_VIEW,
  MENU_VIEW_SORT_CLEAR,
  MENU_VIEW_SORT_DELETE_FIRST,
  MENU_VIEW_SORT_KEEP_FIRST,
  colHeaderTestid,
  rowGroupTestid,
} from "@/testids";
import type { DecisionValue, FileRow as FileRowData, Group } from "@/api/types";

function mkRow(basename: string, decision: DecisionValue): FileRowData {
  return {
    file_path: `/photos/${basename}`,
    basename,
    folder: "/photos",
    action: "REVIEW_DUPLICATE",
    user_decision: decision,
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
    thumbnail_url: `/api/image?path=${encodeURIComponent(`/photos/${basename}`)}&size=128`,
  } as FileRowData;
}

// Server order interleaves the buckets so every sort visibly moves rows.
const SERVER: Array<[string, DecisionValue]> = [
  ["d1.jpg", "delete"],
  ["k1.jpg", ""],
  ["k2.jpg", ""],
];

function groupsWith(decisions: Record<string, DecisionValue> = {}): Group[] {
  const items = SERVER.map(([name, d]) => mkRow(name, decisions[name] ?? d));
  return [{ group_number: 1, member_count: items.length, items }];
}

function seed() {
  act(() => {
    useAppStore.setState({
      manifest: {
        path: "/m/test.db",
        groups: groupsWith(),
        totalGroups: 1,
        totalFiles: SERVER.length,
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
        filterText: "",
        decisionSortSnapshot: null,
        decisionResortSeq: 0,
      },
    });
  });
}

/** Stage a decision the way setDecision's optimistic write leaves the store. */
function stage(basename: string, decision: DecisionValue) {
  act(() => {
    useAppStore.setState((s) => ({
      manifest: { ...s.manifest, groups: groupsWith({ [basename]: decision }) },
    }));
  });
}

const rowOrder = () =>
  screen
    .getAllByTestId(/^row-file-1-/)
    .map((el) => el.getAttribute("data-testid")!.replace("row-file-1-", ""));

const wrapperOf = (basename: string) =>
  screen.getByTestId(`row-file-1-${basename}`).closest<HTMLElement>("[data-index]")!;

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

describe("ResultTree × sort by decision (#923)", () => {
  let restore: (() => void) | undefined;

  beforeEach(() => {
    localStorage.clear();
    useI18nStore.setState({ locale: "en", catalog: {} });
    restore = stubOffsetDims();
    seed();
  });
  afterEach(() => restore?.());

  it("sorts Keep first from the header and spells the order", async () => {
    render(<ResultTree />);
    expect(rowOrder()).toEqual(["d1.jpg", "k1.jpg", "k2.jpg"]);
    await userEvent.click(screen.getByTestId(colHeaderTestid("action")));
    expect(rowOrder()).toEqual(["k1.jpg", "k2.jpg", "d1.jpg"]);
    expect(screen.getByTestId(COL_SORT_SUBLABEL)).toHaveTextContent("· Keep first");
  });

  it("does NOT move a row on a decision change — a stale dot appears instead", async () => {
    render(<ResultTree />);
    await userEvent.click(screen.getByTestId(colHeaderTestid("action")));
    expect(screen.queryByTestId(COL_SORT_STALE_DOT)).toBeNull();

    stage("k1.jpg", "delete"); // the top Keep row becomes a Delete

    expect(rowOrder()).toEqual(["k1.jpg", "k2.jpg", "d1.jpg"]);
    expect(screen.getByTestId(COL_SORT_STALE_DOT)).toHaveAttribute(
      "title",
      "Order is out of date — click to re-sort"
    );
  });

  it("re-sorts on the next header click, slides the rows, and clears the dot", async () => {
    render(<ResultTree />);
    await userEvent.click(screen.getByTestId(colHeaderTestid("action")));
    stage("k1.jpg", "delete");
    await userEvent.click(screen.getByTestId(colHeaderTestid("action")));

    // Still Keep first: the click re-sorted rather than advancing the cycle.
    expect(screen.getByTestId(COL_SORT_SUBLABEL)).toHaveTextContent("· Keep first");
    expect(rowOrder()).toEqual(["k2.jpg", "d1.jpg", "k1.jpg"]);
    expect(screen.queryByTestId(COL_SORT_STALE_DOT)).toBeNull();
    // F3: «rows animate to their new position over 120ms ease-out».
    expect(wrapperOf("k2.jpg").style.transition).toBe(RESORT_TRANSITION);
  });

  it("re-sorts silently on a group collapse — the dot clears, nothing animates", async () => {
    render(<ResultTree />);
    await userEvent.click(screen.getByTestId(colHeaderTestid("action")));
    stage("k1.jpg", "delete");
    expect(screen.getByTestId(COL_SORT_STALE_DOT)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId(rowGroupTestid("1")));
    expect(screen.queryByTestId(COL_SORT_STALE_DOT)).toBeNull();
  });

  it("keeps the View-menu mirror and the header on ONE sort state", async () => {
    const noop = () => {};
    render(
      <>
        <MenuBar
          manifestLoaded
          hasSelection={false}
          locale="en"
          onScan={noop}
          onOpenManifest={noop}
          onSetAction={noop}
          onExecute={noop}
          onExecuteSelected={noop}
          onRemoveFromList={noop}
          onSetLocale={noop}
        />
        <ResultTree />
      </>
    );
    const user = userEvent.setup();

    // Menu → header.
    await user.click(screen.getByTestId(MENU_VIEW));
    await user.click(screen.getByTestId(MENU_VIEW_SORT_DELETE_FIRST));
    expect(screen.getByTestId(COL_SORT_SUBLABEL)).toHaveTextContent("· Delete first");
    expect(rowOrder()).toEqual(["d1.jpg", "k1.jpg", "k2.jpg"]);

    // Header → menu: the third state of the cycle is "cleared".
    await user.click(screen.getByTestId(colHeaderTestid("action")));
    expect(screen.queryByTestId(COL_SORT_SUBLABEL)).toBeNull();
    await user.click(screen.getByTestId(MENU_VIEW));
    expect(within(screen.getByTestId(MENU_VIEW_SORT_KEEP_FIRST)).queryByText("✓")).toBeNull();
    expect(within(screen.getByTestId(MENU_VIEW_SORT_DELETE_FIRST)).queryByText("✓")).toBeNull();
    expect(screen.getByTestId(MENU_VIEW_SORT_CLEAR)).toHaveAttribute("data-disabled");
  });
});
