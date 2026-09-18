// Derived per-group facts the group header shows beside the id (#878, layout
// slice G).
//
// Open questions Q4 settled what a group header may say about itself: the id
// and the count are enough, and anything MORE has to be *derived from fields
// the manifest already sends* — "do not derive a kind label … that requires
// inferring intent from extensions and will be wrong often enough to cost
// trust". Two derivations survived that rule, and both live here as pure
// functions so they can be table-tested without a DOM:
//
//   * the byte total, which is a plain sum;
//   * the folder suffix, which is the shared folder's LEAF when every member
//     sits in one folder, and the folder COUNT when they do not — "the
//     cross-folder case is genuinely useful information, since it is the one
//     where deleting the 'wrong' copy has consequences for someone's folder
//     structure" (Q4).
//
// The shot-date span Q4 also permits is deliberately NOT built here: the
// REPLY lists it as optional polish, and slice G ships the folder suffix only.

import type { FileRow } from "../api/types";

/** Sum of `file_size_bytes` across a group's members. 0 for an empty group. */
export function groupSizeTotal(items: readonly FileRow[]): number {
  return items.reduce((total, item) => total + (item.file_size_bytes || 0), 0);
}

/**
 * What the derived suffix after the count should say.
 *
 *  - `{ kind: "leaf" }`  — every member shares one folder; `leaf` is its last
 *    path segment (`/fake/photos/2021-trip` → `2021-trip`).
 *  - `{ kind: "folders" }` — members span `count` (≥2) distinct folders.
 *  - `null` — nothing honest to say (no members, or no folder on any member).
 *
 * Returns the DATA, not a string: the multi-folder form is a translated,
 * pluralised sentence ("· 2 folders" / 「· 2 個資料夾」) and building it here
 * would drag the i18n catalog into a pure module.
 */
export type GroupFolderSuffix =
  | { kind: "leaf"; leaf: string }
  | { kind: "folders"; count: number };

export function groupFolderSuffix(
  items: readonly FileRow[]
): GroupFolderSuffix | null {
  const folders = new Set<string>();
  for (const item of items) {
    const folder = item.folder;
    if (typeof folder === "string" && folder !== "") folders.add(folder);
  }
  if (folders.size === 0) return null;
  if (folders.size > 1) return { kind: "folders", count: folders.size };

  const [only] = [...folders];
  // Leaf = last non-empty segment, so a trailing separator does not yield "".
  // Both separators are handled: the manifest carries whatever the OS wrote,
  // and a Windows manifest opened on this screen has backslashes in it.
  const segments = only.split(/[\\/]/).filter((s) => s !== "");
  const leaf = segments.length > 0 ? segments[segments.length - 1] : only;
  return { kind: "leaf", leaf };
}
