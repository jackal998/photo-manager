// Result-tree column model — the single registry shared by the sticky
// ColumnHeaderRow and each FileRow so header cells and body cells stay aligned.
//
// Ports the Qt result tree's column set (app/views/constants.py) to the web.
// Only the metadata columns that render a fixed-width cell in FileRow are
// modelled here (thumbnail / decision / lock controls are row-chrome, not
// headed columns). Sort + resize (#685 → s45/s47) operate over THIS registry.

import type { FileRow as FileRowData } from "@/api/types";

// ---------------------------------------------------------------------------
// Column identity
// ---------------------------------------------------------------------------

/** Stable id for each headed/resizable metadata column. Used in testids and
 *  the persisted-width localStorage key, so DO NOT rename without a migration. */
export type ColumnId =
  | "name"
  | "similarity"
  | "action"
  | "score"
  | "dims"
  | "size"
  | "date";

export type SortDirection = "asc" | "desc";

export interface ColumnDef {
  id: ColumnId;
  /** i18n catalog key (mirrors the Qt translations/en.yml::column.* keys). */
  labelKey: string;
  /** English fallback shown before the catalog loads / when the key is absent. */
  labelFallback: string;
  /** Default width in px — matches the prior fixed Tailwind width of the cell. */
  defaultWidth: number;
  /** Clicking the header cell toggles sort on this column (s45 covers name + size). */
  sortable: boolean;
  /** Cell text alignment. Numeric columns right-align like the Qt model. */
  align: "left" | "right";
  /** Render the BODY cell in `font-mono` (layout REPLY L2: "mono returns,
   *  selectively" — size · date · dims · score are machine values that have to
   *  align digit-over-digit down the column). The header label stays sans. */
  mono: boolean;
  /** This column FILLS the row's leftover width instead of taking a fixed box
   *  (layout REPLY L3: "name minimum 160px, then the table scrolls
   *  horizontally"). Exactly one column is flexible; shedding exists to feed
   *  it. Its stored width is the flex BASIS **and** its `min-width`, so the
   *  rendered box is `stored + slack` and never narrower than `stored`. */
  flexible: boolean;
}

// Order here IS the left-to-right render order in both the header and FileRow.
// Widths come from the layout REPLY's "Column budget at 1280×800 with the
// preview open" (2026-09-18): similarity 92, decision 168, score 96, size 72,
// date 112, dims 88, with File Name taking the 160px floor the budget sets for
// it. They replace the ad-hoc Tailwind widths this registry was seeded from.
//
// `action` is the DECISION column since open-questions Q1: the classification
// enum it used to print is fully implied by the similarity badge beside it, so
// it was a column spent on a value that never varies independently — and worse,
// it *looked* like the decision while the real decision control sat unlabelled
// to its right. The classification now lives in that badge's tooltip. The id
// stays `action` on purpose: it is the localStorage key, the `data-col` value
// #735's "Set Action by Field…" pre-fill reads, and the testid suffix s45/s47
// address. This also RESOLVES the #735 mismatch where `rowValuesForSeed` seeded
// the "Action" field from `row.user_decision` while the cell displayed
// `row.action` — cell and seed now name the same thing.
//
// labelKey uses the web.column.* namespace — the only namespace GET /api/i18n
// serves to the browser — so these headers translate in zh_TW (not just render
// the English fallback). The keys mirror the desktop column.* set; web.column.*
// already exists in translations/{en,zh_TW}.yml.
export const COLUMNS: readonly ColumnDef[] = [
  { id: "name", labelKey: "web.column.file_name", labelFallback: "File Name", defaultWidth: 160, sortable: true, align: "left", mono: false, flexible: true },
  { id: "similarity", labelKey: "web.column.similarity", labelFallback: "Similarity", defaultWidth: 92, sortable: false, align: "left", mono: false, flexible: false },
  { id: "action", labelKey: "web.column.action", labelFallback: "Action", defaultWidth: 168, sortable: false, align: "left", mono: false, flexible: false },
  { id: "score", labelKey: "web.column.score", labelFallback: "Score", defaultWidth: 96, sortable: false, align: "right", mono: true, flexible: false },
  { id: "dims", labelKey: "web.column.resolution", labelFallback: "Resolution", defaultWidth: 88, sortable: false, align: "right", mono: true, flexible: false },
  { id: "size", labelKey: "web.column.size", labelFallback: "Size", defaultWidth: 72, sortable: true, align: "right", mono: true, flexible: false },
  { id: "date", labelKey: "web.column.shot_date", labelFallback: "Shot Date", defaultWidth: 112, sortable: false, align: "left", mono: true, flexible: false },
] as const;

/**
 * The style a column's cell takes, in the header and in every row — ONE
 * function so the two can never disagree about a box.
 *
 * The flexible column is `flex: 1 1 <stored>` with `min-width: <stored>`: it
 * fills whatever the shed columns freed, and can never render narrower than the
 * width the user chose. Everything else is a plain fixed box.
 *
 * **What a user sees when they drag the fill column.** Its rendered width is
 * `stored + slack`, and `slack` shrinks as `stored` grows — so a drag that
 * stays inside the current slack moves the STORED width without moving the box.
 * That is ordinary fill-column behaviour (Explorer's last column does it), and
 * it is bounded here only because `visibleColumns` is NEED-BASED: slack is
 * never more than one about-to-be-shed column wide, and the moment the drag
 * exceeds it a column is given up and the width lands on the filename.
 * Measured on the real page (s47, 2026-09-18): a +120px drag took the stored
 * width 160 → 280 and the rendered box 250 → 374 — visibly +124px, because
 * crossing the slack cost `date` its place. Dragging NARROWER inside the slack
 * is the case that stays invisible; that is the fill column's contract, not a
 * broken handle, and `min-width: stored` guarantees the box never renders
 * narrower than the width the user asked for.
 *
 * Under threshold-based shedding this was NOT survivable: slack was whatever
 * two shed columns freed (measured 214px), so the same drag moved the stored
 * width 160 → 280 with the box pinned at 374 and the handle looked dead.
 */
export function columnCellStyle(
  col: ColumnDef,
  width: number
): { width: number } | { flexGrow: number; flexShrink: number; flexBasis: number; minWidth: number } {
  if (!col.flexible) return { width };
  return { flexGrow: 1, flexShrink: 1, flexBasis: width, minWidth: width };
}

/** Default per-column widths keyed by id (derived from COLUMNS). */
export const DEFAULT_COLUMN_WIDTHS: Record<ColumnId, number> = COLUMNS.reduce(
  (acc, c) => {
    acc[c.id] = c.defaultWidth;
    return acc;
  },
  {} as Record<ColumnId, number>
);

/** Minimum width a column can be resized to (px). Guards against a 0-width
 *  column that becomes impossible to grab again. */
export const MIN_COLUMN_WIDTH = 40;

// ---------------------------------------------------------------------------
// Column shedding (layout REPLY L3, "Column budget at 1280×800")
// ---------------------------------------------------------------------------
//
// Shedding is NEED-BASED, not threshold-based: a column is dropped only while
// what is left still does not fit. The REPLY quotes 1200 / 1080 / 940px, but
// those numbers are an OUTPUT of its own budget arithmetic with its own widths
// and chrome — hardcoding them makes the table shed columns it could have
// afforded the moment any width or the gutter changes. So the arithmetic is the
// rule here and the thresholds are derived from it (`shedThresholds()`).
//
// Drop order — least-comparable first:
//   1. "dims"  — resolution is the field most often identical across a
//                duplicate group, so it compares least
//   2. "date"  — dropped after dims because it is a primary sort
//   3. the score cell goes compact (the REPLY also drops the score bar's "keep"
//      label here; that label is a later slice, so today this only narrows the
//      cell — the plumbing is here so the label has nothing left to add)
//
// Shedding is AUTOMATIC and has no user override (orchestrator decision,
// 2026-09-18): a hidden per-column toggle would be a second, invisible source
// of truth for a layout the width already decides.
//
// Nothing becomes unreachable — every dropped column is still rendered in the
// preview pane's metadata table, which is always present. The REPLY also wants
// dropped columns to stay listed as sort options; there is no sort MENU in the
// web header today (sorting is click-the-header only, and neither sheddable
// column is sortable), so there is nothing to keep them listed in — see the PR
// body. Neither `name` nor `size`, the only two sortable columns, is sheddable,
// so a shed can never strand an active sort.

/** Width (px) the Score cell collapses to when even the shed set will not fit. */
export const SCORE_COMPACT_WIDTH = 72;

/** The order columns are given up in, when the row does not fit. */
const SHED_ORDER: readonly ColumnId[] = ["dims", "date"];

// Row chrome that sits in the same flex line as the columns, mirrored from
// FileRow / ColumnHeaderRow. These are the numbers `requiredWidth` cannot see
// from the registry, so a change to either component's markup has to change
// them here too — the shedThresholds() test is what notices if it does not.
//
// They are the COMFORTABLE density's numbers (lib/rowMetrics.ts). Shedding is
// deliberately NOT density-aware: compact is narrower on every one of these
// (36px thumb, 24px padlock, 28px padding, 10px gap), so computing the
// requirement at comfortable over-states it by 22px and sheds a column a
// hair EARLIER than strictly necessary. That is the safe direction — the
// failure mode shedding exists to prevent is a row that overflows, and a
// density-keyed threshold would have to move `visibleColumns` / `isScoreCompact`
// / `shedThresholds` onto a density argument for 22px of width.
/** `px-4` on both sides of the row. */
export const ROW_PADDING_X = 32;
/** `gap-3` between every item in the row. */
export const ROW_GAP = 12;
/** 48px thumbnail (and the header's matching spacer) — REPLY §"Thumbnail". */
export const ROW_THUMB_WIDTH = 48;
/** 28px padlock hit target at the end of the row — REPLY §"Padlock". */
export const ROW_LOCK_WIDTH = 28;

/**
 * The width a row needs to render `cols` without overflowing: the columns at
 * their stored widths (the flexible one at its floor, which is the same
 * number), the thumbnail and padlock chrome, the gaps between all of them, and
 * the row's horizontal padding.
 */
export function requiredWidth(
  cols: readonly ColumnDef[],
  widths: Record<ColumnId, number>,
  scoreCompact = false
): number {
  const colSum = cols.reduce(
    (n, c) =>
      n + (c.id === "score" && scoreCompact ? SCORE_COMPACT_WIDTH : widths[c.id]),
    0
  );
  // thumbnail + every column + padlock, with a gap between each pair.
  const itemCount = cols.length + 2;
  return (
    ROW_PADDING_X +
    ROW_THUMB_WIDTH +
    ROW_LOCK_WIDTH +
    colSum +
    ROW_GAP * (itemCount - 1)
  );
}

export interface ShedPlan {
  /** Columns to render, in registry order. */
  columns: readonly ColumnDef[];
  /** Whether the Score cell renders at `SCORE_COMPACT_WIDTH`. */
  scoreCompact: boolean;
}

/**
 * What a table of `tableWidth` px can afford, given the user's current widths.
 *
 * `null` means "not measured yet" (first paint, or a jsdom/headless layout that
 * reports a 0-width box) and keeps the FULL set — shedding may only ever be
 * driven by a real measurement, never by the absence of one.
 */
export function shedPlan(
  tableWidth: number | null,
  widths: Record<ColumnId, number> = DEFAULT_COLUMN_WIDTHS
): ShedPlan {
  return shedPlanFor(tableWidth, effectiveColumnWidths(tableWidth, widths));
}

/** The raw arithmetic, with no self-healing — `effectiveColumnWidths` needs to
 *  ask "would THESE widths shed it?" without recursing back through healing. */
function shedPlanFor(
  tableWidth: number | null,
  widths: Record<ColumnId, number>
): ShedPlan {
  if (tableWidth === null) return { columns: COLUMNS, scoreCompact: false };
  let columns: readonly ColumnDef[] = COLUMNS;
  for (const id of SHED_ORDER) {
    if (requiredWidth(columns, widths) <= tableWidth) {
      return { columns, scoreCompact: false };
    }
    columns = columns.filter((c) => c.id !== id);
  }
  const scoreCompact = requiredWidth(columns, widths) > tableWidth;
  return { columns, scoreCompact };
}

function isShown(plan: ShedPlan, id: ColumnId): boolean {
  return plan.columns.some((c) => c.id === id);
}

/**
 * The widths shedding actually uses — the stored ones, except where a SHEDDABLE
 * column's own stored width is the only reason it is hidden. Then it falls back
 * to that column's default.
 *
 * This is the self-heal for a width that can otherwise hide a column
 * permanently: a sheddable column dragged wider than the row can pay for
 * disappears, and once it is gone there is no header cell left to grab, no
 * handle to double-click and no way back except making the window wider. A
 * width is not allowed to be the thing that removes its own escape hatch.
 *
 * ONE function, called from two places: `shedPlan` (so the column is back on
 * screen in the same render) and `ResultTree` (which writes the corrected width
 * to the store, so the bad value does not sit in localStorage waiting for the
 * next launch). `columnResizeCeiling` stops new ones being created; this heals
 * blobs written before that ceiling existed.
 */
export function effectiveColumnWidths(
  tableWidth: number | null,
  widths: Record<ColumnId, number>
): Record<ColumnId, number> {
  if (tableWidth === null) return widths;
  let out = widths;
  for (const id of SHED_ORDER) {
    if (isShown(shedPlanFor(tableWidth, out), id)) continue;
    const withDefault = { ...out, [id]: DEFAULT_COLUMN_WIDTHS[id] };
    // Only heal when the DEFAULT would fit. A genuinely narrow table sheds the
    // column at any width, and pretending otherwise would fight the budget.
    if (isShown(shedPlanFor(tableWidth, withDefault), id)) out = withDefault;
  }
  return out;
}

/**
 * The widest a column may be resized to without shedding ITSELF.
 *
 * A resize drag is the one place a width is chosen interactively, and a
 * sheddable column dragged past what the row can pay for would vanish under the
 * cursor mid-drag — taking its own handle with it. `requiredWidth` is linear in
 * the dragged width, so the ceiling is exact: back the requested width off by
 * however much it overflows. Non-sheddable columns have no ceiling (widening
 * `name` is allowed to cost `date` its place — that is the budget working, and
 * `name`'s own handle stays reachable).
 */
export function columnResizeCeiling(
  column: ColumnId,
  tableWidth: number | null,
  widths: Record<ColumnId, number>
): number | null {
  if (tableWidth === null) return null;
  const order = SHED_ORDER.indexOf(column);
  if (order < 0) return null;
  // By the time THIS column is the one at risk, everything shed before it in
  // the order is already gone — so that is the set to measure against, and the
  // ceiling is the width at which the row exactly fills the table.
  const droppedFirst = SHED_ORDER.slice(0, order);
  const cols = COLUMNS.filter((c) => !droppedFirst.includes(c.id));
  const base = requiredWidth(cols, widths) - widths[column];
  return Math.max(MIN_COLUMN_WIDTH, tableWidth - base);
}

/**
 * The width a resize drag may commit for `column`: the requested width, capped
 * at `columnResizeCeiling`. `widths` is the map WITHOUT the requested change —
 * the ceiling depends on the column's neighbours, never on itself.
 */
export function clampResizeWidth(
  column: ColumnId,
  requested: number,
  tableWidth: number | null,
  widths: Record<ColumnId, number>
): number {
  const ceiling = columnResizeCeiling(column, tableWidth, widths);
  if (ceiling === null) return requested;
  return Math.min(requested, ceiling);
}

/** The columns to render for a table of `tableWidth` px. See `shedPlan`. */
export function visibleColumns(
  tableWidth: number | null,
  widths: Record<ColumnId, number> = DEFAULT_COLUMN_WIDTHS
): readonly ColumnDef[] {
  return shedPlan(tableWidth, widths).columns;
}

/** True when the Score cell must render compact. See `shedPlan`. */
export function isScoreCompact(
  tableWidth: number | null,
  widths: Record<ColumnId, number> = DEFAULT_COLUMN_WIDTHS
): boolean {
  return shedPlan(tableWidth, widths).scoreCompact;
}

/**
 * The table widths at which each shed step kicks in, DERIVED from the registry
 * and the row chrome rather than quoted from the design reply.
 *
 * Each value is the smallest table width that still fits the set ABOVE it, so
 * `tableWidth < shedThresholds().dims` is exactly "dims has been dropped".
 * Publishing them is what lets a test pin the behaviour at a boundary without
 * anyone re-typing a number the arithmetic already knows.
 */
export function shedThresholds(
  widths: Record<ColumnId, number> = DEFAULT_COLUMN_WIDTHS
): { dims: number; date: number; scoreCompact: number } {
  const withoutDims = COLUMNS.filter((c) => c.id !== "dims");
  const withoutDate = withoutDims.filter((c) => c.id !== "date");
  return {
    dims: requiredWidth(COLUMNS, widths),
    date: requiredWidth(withoutDims, widths),
    scoreCompact: requiredWidth(withoutDate, widths),
  };
}

// ---------------------------------------------------------------------------
// Sort comparators
// ---------------------------------------------------------------------------
//
// Mirrors the Qt proxy model's SORT_ROLE: File Name sorts as a case-folded
// string (tree_model_builder sets SORT_ROLE = str(name).lower()), Size sorts
// numerically on the raw byte count (SORT_ROLE = int(size)). Sort is applied
// PER GROUP (the Qt tree proxy sorts children within each parent), so the
// comparator only ever orders items inside one group.

function compareName(a: FileRowData, b: FileRowData): number {
  // Code-point order on the lower-cased basename — matches Python's
  // ``sorted(key=str.lower)`` oracle in the s45 driver for ASCII names.
  const al = a.basename.toLowerCase();
  const bl = b.basename.toLowerCase();
  if (al < bl) return -1;
  if (al > bl) return 1;
  return 0;
}

function compareSize(a: FileRowData, b: FileRowData): number {
  return a.file_size_bytes - b.file_size_bytes;
}

/**
 * Build a comparator for the given (column, direction), or null when no
 * sort is active (the default — rows render in server response order, which
 * is what every existing result-tree scenario relies on).
 */
export function makeRowComparator(
  sortColumn: ColumnId | null,
  direction: SortDirection
): ((a: FileRowData, b: FileRowData) => number) | null {
  let base: ((a: FileRowData, b: FileRowData) => number) | null = null;
  if (sortColumn === "name") base = compareName;
  else if (sortColumn === "size") base = compareSize;
  if (base === null) return null;
  const sign = direction === "desc" ? -1 : 1;
  return (a, b) => sign * base(a, b);
}
