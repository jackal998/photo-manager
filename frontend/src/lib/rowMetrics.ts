// The two density value sets for a result-tree row (#878, layout slice R).
//
// Every number here is quoted from the layout REPLY (2026-09-18) — §"Row
// heights" for the box, §"Thumbnail (R6)", §"Decision control (R16)",
// §"Padlock (R17)" and §"Score bar (R15)" for the controls. They live in ONE
// module because three consumers have to agree on them or the screen breaks in
// ways no unit test sees:
//
//   * ResultTree's `estimateSize` — the virtualiser's contract. A row that
//     renders taller than its estimate makes keyboard scroll-into-view land
//     short, and `overscan: 10` hides that for a long time.
//   * FileRow / DecisionControl / LockToggle — what actually renders.
//   * ColumnHeaderRow — its leading thumbnail spacer and trailing lock spacer
//     must be exactly the row's thumbnail and padlock widths, or every column
//     header sits off its column.
//
// Class strings are written out in full, per density, rather than assembled
// from fragments: Tailwind v4 scans source TEXT, so an interpolated
// `h-[${n}px]` compiles to nothing at all with no test going red.

import type { Density } from "./density";

/** Group-header row height (px). Not density-keyed — slice G owns that row. */
export const GROUP_HEADER_HEIGHT = 34;

export interface RowMetrics {
  /** Total row height in px INCLUDING the 1px bottom border — the number the
   *  virtualiser estimates with. */
  height: number;
  /** Extra bottom padding on a group's LAST child, so the group closes with a
   *  breath instead of a butt-joint (REPLY L1). Adds to `height`. */
  lastInGroupExtra: number;
  /** Thumbnail edge (px) — also the header's leading spacer width. */
  thumbWidth: number;
  /** Padlock hit target (px) — also the header's trailing spacer width. */
  lockWidth: number;
  /** Horizontal padding, both sides summed (px). */
  paddingX: number;
  /** Flex gap between row items (px). */
  gap: number;
  /** Whether the score cell shows its "keep" label above the number. */
  showScoreLabel: boolean;

  /** Row box: height + padding + gap, for a row that is NOT its group's last. */
  row: string;
  /** Row box for a group's last child (+6/+4px bottom pad). */
  rowLast: string;
  /** The header bar's padding + gap — must match `row`'s. */
  header: string;
  /** Header's leading thumbnail spacer. */
  headerThumbSpacer: string;
  /** Header's trailing padlock spacer. */
  headerLockSpacer: string;
  /** Thumbnail box. */
  thumb: string;
  /** Decision control's inset track. */
  decisionTrack: string;
  /** One decision segment. */
  decisionSegment: string;
  /** Padlock hit target. */
  lock: string;
}

export const ROW_METRICS: Record<Density, RowMetrics> = {
  // «total row height 72px · thumbnail 48×48 · vertical padding 11px ·
  //  horizontal padding 16px · column gap 12px · last-in-group extra +6px»
  comfortable: {
    height: 72,
    lastInGroupExtra: 6,
    thumbWidth: 48,
    lockWidth: 28,
    paddingX: 32,
    gap: 12,
    showScoreLabel: true,
    row: "h-[72px] gap-3 px-4 py-[11px]",
    rowLast: "h-[78px] gap-3 px-4 pt-[11px] pb-[17px]",
    header: "gap-3 px-4",
    headerThumbSpacer: "w-[48px]",
    headerLockSpacer: "w-[28px]",
    thumb: "h-[48px] w-[48px] rounded-[8px]",
    decisionTrack: "h-[32px] gap-[3px] rounded-[9px] p-[3px]",
    decisionSegment: "h-[26px] px-[10px] rounded-[7px]",
    lock: "h-[28px] w-[28px] rounded-[6px]",
  },
  // «52px · 36×36 · 7px · 14px · 10px · +4px», and the score cell drops its
  // "keep" label but keeps the number (REPLY §"Score bar").
  compact: {
    height: 52,
    lastInGroupExtra: 4,
    thumbWidth: 36,
    lockWidth: 24,
    paddingX: 28,
    gap: 10,
    showScoreLabel: false,
    row: "h-[52px] gap-[10px] px-[14px] py-[7px]",
    rowLast: "h-[56px] gap-[10px] px-[14px] pt-[7px] pb-[11px]",
    header: "gap-[10px] px-[14px]",
    headerThumbSpacer: "w-[36px]",
    headerLockSpacer: "w-[24px]",
    thumb: "h-[36px] w-[36px] rounded-[6px]",
    decisionTrack: "h-[28px] gap-[3px] rounded-[9px] p-[3px]",
    decisionSegment: "h-[22px] px-[8px] rounded-[7px]",
    lock: "h-[24px] w-[24px] rounded-[6px]",
  },
};

/**
 * The height the virtualiser should assume for one virtual row.
 *
 * Two variables, four answers — 72 / 78 / 52 / 56 for file rows. The REPLY
 * calls the row height "one number in `estimateSize`"; it is two per density,
 * because a group's last child carries the closing breath (plan §"Contradictions
 * and gaps found" item 3). Getting this wrong does NOT turn a unit test red: it
 * shows up as arrow-key scroll-into-view landing a row partly under the sticky
 * header, which `overscan` masks until the list is long.
 */
export function estimateRowSize(
  kind: "group-header" | "file",
  isLastInGroup: boolean,
  density: Density
): number {
  if (kind === "group-header") return GROUP_HEADER_HEIGHT;
  const m = ROW_METRICS[density];
  return isLastInGroup ? m.height + m.lastInGroupExtra : m.height;
}
