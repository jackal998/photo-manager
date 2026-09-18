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
