// Sort by decision (#923) — design ruling F3 plus the owner's 2026-09-29
// deviations (recorded to design as "FEEDBACK - F1 F3 implementation
// deviations"). Pure functions only; the store owns the state and ResultTree
// applies the order.
//
// Three rules live here:
//
//   * **The order.** Ascending ("Keep first") is Keep ('') → Skip ('ignore')
//     → Delete; descending ("Delete first") is the reverse. The web decision
//     model has no "undecided" state — '' IS keep (#584) — so F3's
//     none → keep → delete was re-ruled onto the three values that exist.
//   * **Ties keep the server order.** The comparator returns 0 inside a
//     bucket, and `Array.prototype.sort` is stable, so rows with the same
//     decision stay in the order GET /api/manifest sent them — score-descending
//     by default (app/viewmodels/main_vm.py:54-64), or whatever the user's
//     `sorting.defaults` asks for. That is also the whole of #743's
//     "secondary sort by score descending": no separate tie key, by owner
//     decision, so a user's configured defaults are honoured.
//   * **Deferred re-sort.** Rows are ordered by a SNAPSHOT of the decisions
//     taken when the sort was applied, never by the live ones: a decision
//     change must not move a row under a pointer still travelling toward an
//     irreversible verb. A live decision that differs from the snapshot is
//     what makes the order stale.
//
// Lock is not a sort key (F3): a locked row sorts by its decision like any
// other.

import type { DecisionValue, FileRow, Group } from "@/api/types";

/** Bucket position in the ASCENDING ("Keep first") order. */
export const DECISION_SORT_RANK: Readonly<Record<DecisionValue, number>> = {
  "": 0,
  ignore: 1,
  delete: 2,
};

/** file_path → the decision the current Action sort ordered that row by. */
export type DecisionSnapshot = Readonly<Record<string, DecisionValue>>;

/** Capture every row's live decision — the moment a sort (re-)applies. */
export function snapshotDecisions(groups: readonly Group[]): DecisionSnapshot {
  const out: Record<string, DecisionValue> = {};
  for (const group of groups) {
    for (const row of group.items) {
      out[row.file_path] = row.user_decision;
    }
  }
  return out;
}

/**
 * True when some row's live decision no longer matches the decision the
 * order was built from — i.e. the rows on screen are not where a fresh sort
 * would put them. The snapshot covers every loaded row: it is re-taken when
 * the sort is applied, on each re-sort, AND on every manifest load
 * (useAppStore.loadManifest) — the load is the only thing that adds rows, and
 * without that re-take a new manifest's rows would sort by their LIVE
 * decisions and jump on a decision change. A path the snapshot lacks is
 * therefore not expected; it is skipped rather than guessed at.
 */
export function isDecisionSortStale(
  groups: readonly Group[],
  snapshot: DecisionSnapshot
): boolean {
  for (const group of groups) {
    for (const row of group.items) {
      const ordered = snapshot[row.file_path];
      if (ordered !== undefined && ordered !== row.user_decision) return true;
    }
  }
  return false;
}

/**
 * Comparator for the Action sort. Buckets by the SNAPSHOT decision and
 * returns 0 inside a bucket so a stable sort keeps the server order there.
 * The live-decision fallback only covers a path the snapshot lacks, which
 * the manifest-load re-take (see isDecisionSortStale) keeps from happening.
 */
export function makeDecisionComparator(
  direction: "asc" | "desc",
  snapshot: DecisionSnapshot
): (a: FileRow, b: FileRow) => number {
  const sign = direction === "desc" ? -1 : 1;
  const rank = (row: FileRow) =>
    DECISION_SORT_RANK[snapshot[row.file_path] ?? row.user_decision];
  return (a, b) => sign * (rank(a) - rank(b));
}
