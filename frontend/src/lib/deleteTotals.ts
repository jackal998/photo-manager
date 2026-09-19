// The two numbers the status bar exists for (#906, #878 layout slice TB).
//
// REPLY §"Status bar (F11, F12)": «The reclaim total and the delete count are
// the two numbers that make the status bar worth its 30px — they are the
// running answer to "is this session worth continuing"». The same count also
// rides in the danger CTA's label, because «it is the difference between a
// button that deletes something and a button that deletes *four things*, and
// it is read right before the confirm modal».
//
// Derived from the loaded manifest on every render rather than kept as a
// counter that actions increment: a counter has to be corrected by every write
// path (row control, bulk verb, keep-best, undo, Set-Action dialog, a manifest
// reload) and the first one that forgets shows the user a number that is wrong
// in the direction of "fewer files than I am about to delete".

import type { Group } from "@/api/types";

export interface DeleteTotals {
  /** Rows whose `user_decision` is `delete` across the WHOLE manifest. */
  count: number;
  /** Byte sum of those rows — what Execute would reclaim. */
  bytes: number;
}

/**
 * Count the delete-marked rows and sum their sizes.
 *
 * Counts the whole manifest, NOT the filtered view: this is the number Execute
 * will act on, and a figure that shrank when someone typed in the filter box
 * would understate a destructive action.
 */
export function deleteTotals(groups: readonly Group[]): DeleteTotals {
  let count = 0;
  let bytes = 0;
  for (const group of groups) {
    for (const row of group.items) {
      if (row.user_decision !== "delete") continue;
      count += 1;
      // A row whose size never made it into the manifest contributes 0 rather
      // than NaN — one missing column must not blank the whole reclaim figure.
      bytes += Number.isFinite(row.file_size_bytes) ? row.file_size_bytes : 0;
    }
  }
  return { count, bytes };
}
