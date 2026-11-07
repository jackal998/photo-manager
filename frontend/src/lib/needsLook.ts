// "Needs your look" (PR-3) — which rows and groups sit below the confirm
// threshold the user chose in the scan dialog.
//
// The backend computes each row's level (`confidence_level`); this module only
// compares it with the threshold. It never copies an English label. A row with
// no level (null / absent: a Ref-tier passenger or an old shape) is ignored, so
// it never flags its group. An unknown level flags the row, so a value the
// client does not recognise stays visible rather than hiding a decision.

import type { ConfirmBelow, FileRow, Group } from "@/api/types";

const LEVEL_RANK: Readonly<Record<"low" | "medium" | "high", number>> = {
  low: 0,
  medium: 1,
  high: 2,
};

/** True when the row's level ranks under `below`; see the module header. */
export function rowNeedsLook(
  row: Pick<FileRow, "confidence_level">,
  below: ConfirmBelow
): boolean {
  const level: string | null | undefined = row.confidence_level;
  if (level === undefined || level === null) return false;
  if (!Object.prototype.hasOwnProperty.call(LEVEL_RANK, level)) return true;
  return LEVEL_RANK[level as "low" | "medium" | "high"] < LEVEL_RANK[below];
}

/** True when any row of the group needs a look under `below`. */
export function groupNeedsLook(group: Group, below: ConfirmBelow): boolean {
  return group.items.some((row) => rowNeedsLook(row, below));
}

/** The number of GROUPS (not rows) that need a look under `below`. */
export function countNeedsLook(
  groups: readonly Group[],
  below: ConfirmBelow
): number {
  return groups.filter((group) => groupNeedsLook(group, below)).length;
}
