// Sort by decision (#923) — the order, the tie rule (#743) and the stale rule.
// Every case sorts with the same `[...items].sort(cmp)` ResultTree uses, so a
// stable-sort assumption is exercised, not just asserted.

import { describe, it, expect } from "vitest";

import type { DecisionValue, FileRow, Group } from "@/api/types";
import {
  isDecisionSortStale,
  makeDecisionComparator,
  snapshotDecisions,
} from "./decisionSort";
import { makeRowComparator } from "./resultColumns";

function mk(
  basename: string,
  decision: DecisionValue,
  score: number | null = null,
  locked = false
): FileRow {
  return {
    file_path: `/p/${basename}`,
    basename,
    user_decision: decision,
    is_locked: locked,
    score,
  } as FileRow;
}

function group(items: FileRow[]): Group {
  return { group_number: 1, member_count: items.length, items } as Group;
}

const names = (rows: FileRow[]) => rows.map((r) => r.basename);

describe("makeDecisionComparator — the order (owner ruling on F3)", () => {
  // Server order deliberately interleaves the buckets.
  const server = [
    mk("d1", "delete"),
    mk("k1", ""),
    mk("s1", "ignore"),
    mk("k2", ""),
    mk("d2", "delete"),
  ];
  const snap = snapshotDecisions([group(server)]);

  it("ascending is Keep → Skip → Delete", () => {
    const out = [...server].sort(makeDecisionComparator("asc", snap));
    expect(names(out)).toEqual(["k1", "k2", "s1", "d1", "d2"]);
  });

  it("descending is Delete → Skip → Keep", () => {
    const out = [...server].sort(makeDecisionComparator("desc", snap));
    expect(names(out)).toEqual(["d1", "d2", "s1", "k1", "k2"]);
  });

  it("does not use the lock as a key — a locked row sorts by its decision", () => {
    const rows = [mk("k-locked", "", null, true), mk("d", "delete"), mk("k", "")];
    const out = [...rows].sort(
      makeDecisionComparator("asc", snapshotDecisions([group(rows)]))
    );
    expect(names(out)).toEqual(["k-locked", "k", "d"]);
  });
});

// #743's "within-group secondary sort by score descending" is satisfied by the
// server order itself: GET /api/manifest sends each group score-descending
// (app/viewmodels/main_vm.py:54-64) or in the user's `sorting.defaults`, and a
// stable sort keeps it inside a bucket. The owner chose that over a hard score
// tie key so configured defaults are honoured — so the pin is "server order
// wins ties", including when the server order is NOT score-descending.
describe("ties keep the server order (#743 pin)", () => {
  it("keeps a bucket in server order even when its scores ascend", () => {
    const server = [
      mk("keep-low", "", 0.2),
      mk("del", "delete", 0.99),
      mk("keep-high", "", 0.9),
    ];
    const out = [...server].sort(
      makeDecisionComparator("asc", snapshotDecisions([group(server)]))
    );
    // A score tie key would put keep-high first; the server order does not.
    expect(names(out)).toEqual(["keep-low", "keep-high", "del"]);
  });

  it("reaches that comparator through makeRowComparator('action')", () => {
    const server = [mk("del", "delete", 0.9), mk("keep-a", "", 0.1), mk("keep-b", "", 0.8)];
    const cmp = makeRowComparator("action", "asc", snapshotDecisions([group(server)]));
    expect(cmp).not.toBeNull();
    expect(names([...server].sort(cmp!))).toEqual(["keep-a", "keep-b", "del"]);
  });
});

describe("deferred re-sort — the snapshot, not the live decision, orders rows", () => {
  it("leaves a row where it was when its live decision changes", () => {
    const server = [mk("a", ""), mk("b", ""), mk("c", "delete")];
    const snap = snapshotDecisions([group(server)]);
    // The user stages Delete on the top Keep row — live data changes.
    const live = [mk("a", "delete"), mk("b", ""), mk("c", "delete")];
    const out = [...live].sort(makeDecisionComparator("asc", snap));
    expect(names(out)).toEqual(["a", "b", "c"]);
  });

  it("is stale exactly when a live decision differs from the snapshot", () => {
    const server = [mk("a", ""), mk("b", "ignore")];
    const snap = snapshotDecisions([group(server)]);
    expect(isDecisionSortStale([group(server)], snap)).toBe(false);
    expect(isDecisionSortStale([group([mk("a", "delete"), mk("b", "ignore")])], snap)).toBe(
      true
    );
    // Changed back: the rows ARE where a fresh sort puts them again.
    expect(isDecisionSortStale([group([mk("a", ""), mk("b", "ignore")])], snap)).toBe(false);
  });

  it("does not call a row the snapshot never saw stale", () => {
    const snap = snapshotDecisions([group([mk("a", "")])]);
    expect(isDecisionSortStale([group([mk("a", ""), mk("new", "delete")])], snap)).toBe(
      false
    );
  });
});
