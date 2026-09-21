// The scan summary folded into the menu bar (#878, layout slice TB — REPLY
// L5 "Titlebar vs menu bar (F3/F4)").
//
// «Two stacked chrome bars cost 73px of an 800px viewport and give the user
// nothing the other does not», so the prototype's titlebar is not drawn and its
// one piece of content — "1,284 photos · 3 groups · Pictures Library" — moves
// into the menu bar, right-aligned.
//
// The counts come straight off the manifest. The SOURCE NAME does not exist as
// a field: E7 ("recent sources") was dropped by design, deliberately including
// its scan-history store — «building scan history to feed a list means a new
// persisted store, invalidation when folders move or vanish, and a stale-path
// failure mode on the app's first screen». So the name is DERIVED from data
// already loaded, which cannot go stale: the deepest folder every row shares.
// A library scanned from one tree names that tree; a manifest whose rows span
// unrelated roots has no honest shared name and falls back to the manifest
// file's own.

import type { Group } from "@/api/types";

/** Split a path on either separator — a Windows manifest opened on this screen
 *  carries backslashes, a NAS one forward slashes, and both reach this UI. */
function segmentsOf(path: string): string[] {
  return path.split(/[\\/]/).filter((s) => s !== "");
}

/** Manifest file name without its extension, or null when no path is loaded. */
function manifestName(manifestPath: string | null): string | null {
  if (manifestPath === null) return null;
  const segments = segmentsOf(manifestPath);
  if (segments.length === 0) return null;
  const leaf = segments[segments.length - 1];
  const dot = leaf.lastIndexOf(".");
  return dot > 0 ? leaf.slice(0, dot) : leaf;
}

/**
 * Name of what is being reviewed: the leaf of the deepest folder every row
 * shares, else the manifest file's own name, else null.
 *
 * Walks rows only until the shared prefix is empty, so the mixed-roots case —
 * the one with the most rows to walk — is also the one that exits first.
 */
export function scanSourceName(
  groups: readonly Group[],
  manifestPath: string | null
): string | null {
  let common: string[] | null = null;
  outer: for (const group of groups) {
    for (const row of group.items) {
      const folder = row.folder;
      if (typeof folder !== "string" || folder === "") continue;
      const segments = segmentsOf(folder);
      if (common === null) {
        common = segments;
        continue;
      }
      let i = 0;
      while (i < common.length && i < segments.length && common[i] === segments[i]) {
        i += 1;
      }
      common = common.slice(0, i);
      if (common.length === 0) break outer;
    }
  }
  if (common !== null && common.length > 0) return common[common.length - 1];
  return manifestName(manifestPath);
}
