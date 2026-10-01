// Sort by decision (#923) — the store transitions a user drives from the
// Action header and the View menu. Decisions are changed by writing the
// manifest directly: that is the state `setDecision`'s optimistic update
// produces, without the network round trip it would need here.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { act } from "@testing-library/react";

// Only the two network calls a manifest load and a decision write make; the
// rest of the client module stays real.
const api = vi.hoisted(() => ({ getManifest: vi.fn(), patchDecisions: vi.fn() }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, getManifest: api.getManifest, patchDecisions: api.patchDecisions };
});

import { useAppStore } from "./useAppStore";
import type { DecisionValue, FileRow, Group } from "@/api/types";
import { DEFAULT_COLUMN_WIDTHS, makeRowComparator } from "@/lib/resultColumns";
import { DEFAULT_PANEL_WIDTHS } from "@/lib/panelWidths";
import { isDecisionSortStale } from "@/lib/decisionSort";

function mk(basename: string, decision: DecisionValue): FileRow {
  return { file_path: `/p/${basename}`, basename, user_decision: decision } as FileRow;
}

function setGroups(items: FileRow[]) {
  const groups: Group[] = [{ group_number: 1, member_count: items.length, items } as Group];
  act(() => {
    useAppStore.setState((s) => ({ manifest: { ...s.manifest, groups } }));
  });
}

const view = () => useAppStore.getState().resultView;
const store = () => useAppStore.getState();

beforeEach(() => {
  localStorage.clear();
  act(() => {
    useAppStore.setState({
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
  setGroups([mk("a", ""), mk("b", "delete")]);
});

describe("Action header cycle (F3)", () => {
  it("goes asc (Keep first) → desc (Delete first) → cleared", () => {
    act(() => store().toggleSort("action"));
    expect([view().sortColumn, view().sortDirection]).toEqual(["action", "asc"]);
    act(() => store().toggleSort("action"));
    expect([view().sortColumn, view().sortDirection]).toEqual(["action", "desc"]);
    act(() => store().toggleSort("action"));
    expect(view().sortColumn).toBeNull();
    expect(view().decisionSortSnapshot).toBeNull();
  });

  it("holds ONE sort state: Action replaces a name sort and vice versa", () => {
    act(() => store().toggleSort("name"));
    act(() => store().toggleSort("action"));
    expect([view().sortColumn, view().sortDirection]).toEqual(["action", "asc"]);
    // Switching to Action captures the decisions it orders by …
    expect(view().decisionSortSnapshot).toEqual({ "/p/a": "", "/p/b": "delete" });
    // … and switching away drops them.
    act(() => store().toggleSort("size"));
    expect(view().sortColumn).toBe("size");
    expect(view().decisionSortSnapshot).toBeNull();
  });

  it("re-sorts a STALE order in place instead of advancing the cycle", () => {
    act(() => store().toggleSort("action"));
    setGroups([mk("a", "delete"), mk("b", "delete")]); // a decision changed
    act(() => store().toggleSort("action"));
    // Still Keep first — the click was spent on the re-sort the dot offered.
    expect([view().sortColumn, view().sortDirection]).toEqual(["action", "asc"]);
    expect(view().decisionSortSnapshot).toEqual({ "/p/a": "delete", "/p/b": "delete" });
    expect(view().decisionResortSeq).toBe(1);
  });
});

describe("View-menu mirror — same single state as the header", () => {
  it("applies Delete first, and the header's next click clears it", () => {
    act(() => store().setDecisionSort("desc"));
    expect([view().sortColumn, view().sortDirection]).toEqual(["action", "desc"]);
    act(() => store().toggleSort("action"));
    expect(view().sortColumn).toBeNull();
  });

  it("Clear sort clears whatever sort is active", () => {
    act(() => store().toggleSort("size"));
    act(() => store().setDecisionSort(null));
    expect(view().sortColumn).toBeNull();
  });

  it("re-picking the active order re-sorts, animating only when it was stale", () => {
    act(() => store().setDecisionSort("asc"));
    act(() => store().setDecisionSort("asc"));
    expect(view().decisionResortSeq).toBe(0);
    setGroups([mk("a", "ignore"), mk("b", "delete")]);
    act(() => store().setDecisionSort("asc"));
    expect(view().decisionResortSeq).toBe(1);
    expect(view().decisionSortSnapshot?.["/p/a"]).toBe("ignore");
  });
});

describe("silent re-sort triggers (F3) and persistence", () => {
  it("a filter or density change re-sorts silently — no animation signal", () => {
    act(() => store().toggleSort("action"));
    setGroups([mk("a", "delete"), mk("b", "delete")]);
    act(() => store().setFilterText("a"));
    expect(view().decisionSortSnapshot?.["/p/a"]).toBe("delete");
    setGroups([mk("a", "ignore"), mk("b", "delete")]);
    act(() => store().setDensity("compact"));
    expect(view().decisionSortSnapshot?.["/p/a"]).toBe("ignore");
    expect(view().decisionResortSeq).toBe(0);
  });

  it("never writes the Action sort to localStorage", () => {
    act(() => store().setDecisionSort("desc"));
    act(() => store().setDensity("compact"));
    const stored = Object.keys(localStorage).map((k) => `${k}=${localStorage.getItem(k)}`);
    expect(stored.join("\n")).not.toMatch(/action|sort/i);
  });
});

// Review H1 on #949: `loadManifest` keeps an Action sort (one sort state) but
// used to keep the OLD manifest's snapshot too — so a new manifest's rows
// sorted by their LIVE decisions and jumped buckets on a decision change, and
// re-opened rows sorted by stale decisions with the dot already lit.
describe("a manifest load starts a fresh Action sort (review H1)", () => {
  function manifestOf(items: FileRow[]) {
    return {
      manifest_path: "/m/next.db",
      groups: [{ group_number: 1, member_count: items.length, items }],
      total_groups: 1,
      total_files: items.length,
    };
  }

  /** The order ResultTree renders: the store's comparator over the group. */
  function order(): string[] {
    const v = view();
    const cmp = makeRowComparator(v.sortColumn, v.sortDirection, v.decisionSortSnapshot);
    const items = store().manifest.groups[0].items;
    return (cmp ? [...items].sort(cmp) : items).map((r) => r.basename);
  }

  const stale = () => {
    const snapshot = view().decisionSortSnapshot;
    return snapshot !== null && isDecisionSortStale(store().manifest.groups, snapshot);
  };

  it("keeps a NEW manifest's rows still when a decision changes after the load", async () => {
    act(() => store().toggleSort("action")); // Keep first, on the previous manifest
    api.getManifest.mockResolvedValueOnce(
      manifestOf([mk("x1", ""), mk("x2", ""), mk("x3", "delete")])
    );
    await act(async () => {
      await store().loadManifest("/m/next.db");
    });
    expect(order()).toEqual(["x1", "x2", "x3"]);
    expect(stale()).toBe(false);

    api.patchDecisions.mockResolvedValueOnce(undefined);
    await act(async () => {
      await store().setDecision("/p/x1", "delete");
    });
    // x1 must NOT jump into the Delete bucket under the pointer (F3) …
    expect(order()).toEqual(["x1", "x2", "x3"]);
    // … the order is stale instead, which is what lights the dot.
    expect(stale()).toBe(true);
  });

  it("re-opens the same paths with new decisions un-stale, in the new order", async () => {
    act(() => store().toggleSort("action")); // snapshot: a = Keep, b = Delete
    api.getManifest.mockResolvedValueOnce(manifestOf([mk("a", "delete"), mk("b", "")]));
    await act(async () => {
      await store().loadManifest("/m/next.db");
    });
    expect(stale()).toBe(false);
    expect(order()).toEqual(["b", "a"]);
  });
});
