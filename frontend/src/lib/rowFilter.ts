// Toolbar filter (#878, layout slice TB — REPLY §"Toolbar (F5, F6, F8, F9)").
//
// The REPLY specs the CONTROL ("Filter photos…", 34px, leading ⌕) but not what
// it matches, and the plan flagged that as the slice's main invention risk. The
// answer settled with the orchestrator, written here because it is the part a
// reader cannot recover from the CSS:
//
//   * **Case-insensitive substring over the basename AND the folder path.** A
//     dedupe review is driven by where a file came from at least as often as by
//     what it is called ("everything under 2019/Taiwan"), so matching only the
//     leaf would make the control useless for the query people actually have.
//   * **Groups with no matching row disappear**, rather than showing as empty
//     headers. A header with nothing under it is a promise of rows that are not
//     there, and at 40,000 files the header list alone is the noise.
//   * **View-only.** It never touches a decision, a lock, or the manifest, and
//     it is NOT persisted: a filter that survived a reload would hide rows with
//     no visible cause, which is the failure mode of every "why is my list
//     empty" bug report. Clearing the box restores everything, exactly.
//
// The delete counter, the reclaim total and the Delete-N CTA deliberately do
// NOT read the filtered list — they count the whole manifest, because that is
// what Execute will act on. A filter that silently shrank the danger button's
// number would be the one place this control could cost a user data.

import type { Group } from "@/api/types";

/** True when `row`'s basename or folder contains `needle` (already lowercased). */
function rowMatches(basename: string, folder: string, needle: string): boolean {
  return (
    basename.toLowerCase().includes(needle) ||
    folder.toLowerCase().includes(needle)
  );
}

/**
 * Apply the toolbar filter to the review list.
 *
 * Returns `groups` BY IDENTITY when the filter is empty (or whitespace only),
 * so the unfiltered render is reference-equal to the store's own array and the
 * memo chain downstream — `orderedItemsByGroup`, `vrows`, every row's props —
 * does not re-run for users who never type in the box.
 *
 * `member_count` on a filtered group is recomputed from the surviving rows, so
 * the group header's "5 files" and its derived size total describe what is on
 * screen rather than what is behind the filter.
 */
export function filterGroups(
  groups: readonly Group[],
  filterText: string
): Group[] {
  const needle = filterText.trim().toLowerCase();
  if (needle === "") return groups as Group[];

  const out: Group[] = [];
  for (const group of groups) {
    const items = group.items.filter((row) =>
      rowMatches(row.basename, row.folder, needle)
    );
    if (items.length === 0) continue;
    out.push({
      group_number: group.group_number,
      member_count: items.length,
      items,
    });
  }
  return out;
}

/**
 * The selected paths that are actually ON SCREEN under the current filter.
 *
 * The selection survives a filter (typing in the box must not destroy what the
 * user picked, and clearing it must give the picks back), so `selectedPaths`
 * can name rows nobody can see. Every surface that COUNTS or ACTS on the
 * selection has to agree about that, or the toolbar says "Set 5 selected:" and
 * the verb writes to two rows the user cannot point at — a bulk write with no
 * visible extent, which is precisely what L5's counted label exists to prevent.
 *
 * One function, two callers — `Toolbar`'s label and `applyBulkDecision` — so
 * the number in the label and the number the write touches cannot drift apart.
 * Order follows the SELECTION, not the tree, matching the unfiltered case.
 *
 * Returns `selectedPaths` by identity when the filter is empty: the common
 * case pays nothing, and the array stays reference-stable for React.
 */
export function visibleSelectedPaths(
  groups: readonly Group[],
  filterText: string,
  selectedPaths: readonly string[]
): readonly string[] {
  if (filterText.trim() === "") return selectedPaths;
  const visible = new Set<string>();
  for (const group of filterGroups(groups, filterText)) {
    for (const row of group.items) visible.add(row.file_path);
  }
  return selectedPaths.filter((path) => visible.has(path));
}
