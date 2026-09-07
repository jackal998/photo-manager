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
}

// Order here IS the left-to-right render order in both the header and FileRow.
// Widths mirror the prior fixed Tailwind classes: w-40=160, w-16=64, w-24=96,
// w-12=48, w-20=80. Keeping them identical means the default (un-resized)
// layout is pixel-identical to before this change — zero visual churn for the
// ~21 scenarios that read rows by testid.
// labelKey uses the web.column.* namespace — the only namespace GET /api/i18n
// serves to the browser — so these headers translate in zh_TW (not just render
// the English fallback). The keys mirror the desktop column.* set; web.column.*
// already exists in translations/{en,zh_TW}.yml.
export const COLUMNS: readonly ColumnDef[] = [
  { id: "name", labelKey: "web.column.file_name", labelFallback: "File Name", defaultWidth: 160, sortable: true, align: "left" },
  { id: "similarity", labelKey: "web.column.similarity", labelFallback: "Similarity", defaultWidth: 64, sortable: false, align: "left" },
  { id: "action", labelKey: "web.column.action", labelFallback: "Action", defaultWidth: 96, sortable: false, align: "left" },
  { id: "score", labelKey: "web.column.score", labelFallback: "Score", defaultWidth: 48, sortable: false, align: "right" },
  { id: "dims", labelKey: "web.column.resolution", labelFallback: "Resolution", defaultWidth: 80, sortable: false, align: "left" },
  { id: "size", labelKey: "web.column.size", labelFallback: "Size", defaultWidth: 64, sortable: true, align: "right" },
  { id: "date", labelKey: "web.column.shot_date", labelFallback: "Shot Date", defaultWidth: 96, sortable: false, align: "left" },
] as const;

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
