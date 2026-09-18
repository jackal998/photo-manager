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
  { id: "name", labelKey: "web.column.file_name", labelFallback: "File Name", defaultWidth: 160, sortable: true, align: "left", mono: false },
  { id: "similarity", labelKey: "web.column.similarity", labelFallback: "Similarity", defaultWidth: 92, sortable: false, align: "left", mono: false },
  { id: "action", labelKey: "web.column.action", labelFallback: "Action", defaultWidth: 168, sortable: false, align: "left", mono: false },
  { id: "score", labelKey: "web.column.score", labelFallback: "Score", defaultWidth: 96, sortable: false, align: "right", mono: true },
  { id: "dims", labelKey: "web.column.resolution", labelFallback: "Resolution", defaultWidth: 88, sortable: false, align: "right", mono: true },
  { id: "size", labelKey: "web.column.size", labelFallback: "Size", defaultWidth: 72, sortable: true, align: "right", mono: true },
  { id: "date", labelKey: "web.column.shot_date", labelFallback: "Shot Date", defaultWidth: 112, sortable: false, align: "left", mono: true },
] as const;

// Why File Name is a FIXED, resizable column and not the flex-to-fill column
// the L3 budget draws ("name → 169px / 281px as columns shed"):
//
// A `flex-grow: 1` column's rendered width is `basis + slack`, and `slack` is
// `table − Σ(all bases) − chrome` — so growing its basis shrinks the slack by
// the same amount and the BOX never moves. Measured on the real page (s47,
// 2026-09-18): dragging File Name +120px took its stored width 160 → 280 while
// its rendered box stayed 374px. A resize handle that visibly does nothing is
// a worse regression than an unused gutter, and #685's resize + s47's
// cross-launch persistence are shipped, used behaviour.
//
// The consequence, stated plainly for the layout owner: when columns shed, the
// freed width currently becomes empty space at the right of the row rather than
// filename. Fixing that properly means picking one of the two shapes real
// tables use — a non-resizable fill column, or resize-redistributes-against-
// neighbours — and neither is a slice-C-sized change.

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
// The fixed columns plus their gaps and the thumbnail gutter do not fit the
// ~945px the table gets at 1280×800 with the preview open. Rather than let the
// filename truncate to nothing (or the whole row scroll sideways), the table
// SHEDS the least-comparable columns as its own width falls:
//
//   < 1200px  drop "dims"  — resolution is the field most often identical
//                            across a duplicate group, so it compares least
//   < 1080px  drop "date"  — dropped after dims because it is a primary sort
//   <  940px  the score cell goes compact (the REPLY drops the score bar's
//             "keep" label here and shrinks the cell to 72px; the label itself
//             is a later slice, so this threshold currently only narrows the
//             cell — the plumbing is here so the label has nothing to add)
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
export const SHED_THRESHOLDS = {
  /** Below this table width (px) the Resolution column is not rendered. */
  dims: 1200,
  /** Below this table width (px) the Shot Date column is not rendered. */
  date: 1080,
  /** Below this table width (px) the Score cell renders compact. */
  scoreCompact: 940,
} as const;

/** Width (px) the Score cell collapses to under `SHED_THRESHOLDS.scoreCompact`. */
export const SCORE_COMPACT_WIDTH = 72;

/**
 * The columns to render for a table of `tableWidth` px, in registry order.
 *
 * `null` means "not measured yet" (first paint, or a jsdom/headless layout that
 * reports a 0-width box) and renders the FULL set — shedding may only ever be
 * driven by a real measurement, never by the absence of one.
 */
export function visibleColumns(tableWidth: number | null): readonly ColumnDef[] {
  if (tableWidth === null) return COLUMNS;
  return COLUMNS.filter((c) => {
    if (c.id === "dims") return tableWidth >= SHED_THRESHOLDS.dims;
    if (c.id === "date") return tableWidth >= SHED_THRESHOLDS.date;
    return true;
  });
}

/** True when the Score cell must render compact (see `SHED_THRESHOLDS`). */
export function isScoreCompact(tableWidth: number | null): boolean {
  if (tableWidth === null) return false;
  return tableWidth < SHED_THRESHOLDS.scoreCompact;
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
