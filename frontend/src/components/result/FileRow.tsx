// File row inside a group: thumbnail + metadata columns + decision + lock.

import type { MouseEvent } from "react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/useT";
import {
  similarityLabel,
  classificationLabel,
  formatBytes,
  formatScore,
  formatDate,
  formatDims,
} from "@/lib/format";
import type { FileRow as FileRowData } from "@/api/types";
import {
  COLUMNS,
  SCORE_COMPACT_WIDTH,
  columnCellStyle,
  type ColumnId,
} from "@/lib/resultColumns";
import { scoreBarWidth } from "@/lib/scoreBar";
import {
  SIMILARITY_BADGE,
  similarityBadgeBorderClass,
  similarityBadgeState,
} from "@/lib/similarityBadge";
import { DEFAULT_DENSITY, type Density } from "@/lib/density";
import { ROW_METRICS } from "@/lib/rowMetrics";
import { DecisionControl } from "./DecisionControl";
import { LockToggle } from "./LockToggle";
import {
  rowFileTestid,
  rowDecisionTestid,
  rowLockTestid,
} from "@/testids";

// Alignment + mono are properties of the COLUMN, not of this component: the
// header reads the same registry, so deriving the body cell's classes from it
// is what stops the two drifting (layout REPLY L2 — size · date · dims · score
// are machine values and must align digit-over-digit down the column).
const COLUMN_BY_ID = Object.fromEntries(
  COLUMNS.map((c) => [c.id, c])
) as Record<ColumnId, (typeof COLUMNS)[number]>;

function cellTypeClass(id: ColumnId): string {
  const col = COLUMN_BY_ID[id];
  return cn(col.mono && "font-mono", col.align === "right" && "text-right");
}

interface FileRowProps {
  row: FileRowData;
  groupId: string;
  /** The row's numeric group_number (#744) — Apply best-copy is group-scoped. */
  groupNumber: number;
  /** Per-column widths from the result-view store — keeps each cell aligned
   *  with the ColumnHeaderRow above and honours user resizes (#685 / s47). */
  columnWidths: Record<ColumnId, number>;
  /** Columns the table width currently affords (ResultTree computes it from
   *  `visibleColumns`). Omitted = render every column. */
  visibleCols?: ReadonlySet<ColumnId>;
  /** The <940px score-cell collapse (see `isScoreCompact`). ResultTree also
   *  raises it for the COMPACT density, whose score cell is the same 72px and
   *  likewise drops the "keep" label. */
  scoreCompact?: boolean;
  /** Row density (#878 layout slice R). Every metric below is keyed on it —
   *  see lib/rowMetrics.ts, which the virtualiser's `estimateSize` reads too. */
  density?: Density;
  onDecision: (filePath: string, value: import("@/api/types").DecisionValue) => void;
  onLock: (filePath: string, locked: boolean) => void;
  onSelect?: (
    filePath: string,
    mods: { ctrl: boolean; shift: boolean }
  ) => void;
  onOpenFullRes?: (filePath: string) => void;
  /** `col` (#735) is the clicked column's key, resolved from the nearest
   *  `[data-col]` ancestor of the click target — undefined when the click
   *  landed outside a metadata cell (e.g. the thumbnail, decision, or lock
   *  controls). */
  onContextMenu?: (
    filePath: string,
    isLocked: boolean,
    groupNumber: number,
    x: number,
    y: number,
    col?: string
  ) => void;
  isSelected?: boolean;
  /** True on the group's last visible child (#878). The Daylight group frame
   *  is closed by a border under this row — it has to come from row DATA
   *  because the tree is virtualised, so a CSS sibling selector would only
   *  see the handful of rows currently mounted. */
  isLastInGroup?: boolean;
}

export function FileRow({ row, groupId, groupNumber, columnWidths, visibleCols, scoreCompact = false, density = DEFAULT_DENSITY, onDecision, onLock, onSelect, onOpenFullRes, onContextMenu, isSelected, isLastInGroup }: FileRowProps) {
  const t = useT();
  const metrics = ROW_METRICS[density];
  const simLabel = similarityLabel(row.similarity, t);
  const shows = (id: ColumnId) => visibleCols === undefined || visibleCols.has(id);
  // Q1 — the classification enum lost its column (it never varies independently
  // of the badge beside it). It stays REACHABLE here: the badge's tooltip says
  // it in the legend's own words, via the same web.classification.* keys the
  // column used, so nothing the column carried became unreachable.
  const classification = classificationLabel(row.action, t);
  // Daylight 5-state similarity badge (#878) — colour + border style + weight,
  // so the state survives a grayscale render. See lib/similarityBadge.ts.
  const badge = SIMILARITY_BADGE[similarityBadgeState(row.similarity)];
  // A row staged for deletion gets the soft red wash and a struck-through
  // filename, so the lowest-salience decision on screen becomes the loudest.
  const isDeleting = row.user_decision === "delete";
  // A thumbnail that fails to decode falls back to the warm hatch rather than
  // to the browser's broken-image glyph (REPLY §"Thumbnail (R6)"). Held as
  // state because the failure only becomes known when the <img> errors.
  const [thumbFailed, setThumbFailed] = useState(false);

  function handleClick(e: MouseEvent) {
    // Ctrl/Cmd toggles, Shift extends a range; a plain click replaces. The
    // store interprets the modifiers (ResultTree.handleRowSelect).
    onSelect?.(row.file_path, {
      ctrl: e.ctrlKey || e.metaKey,
      shift: e.shiftKey,
    });
  }

  function handleDoubleClick() {
    onOpenFullRes?.(row.file_path);
  }

  function handleContextMenu(e: MouseEvent) {
    e.preventDefault();
    // #735 — resolve the clicked column (if any) from the nearest [data-col]
    // ancestor so the context menu can pre-fill "Set Action by Field…".
    const col =
      (e.target as HTMLElement).closest("[data-col]")?.getAttribute("data-col") ??
      undefined;
    onContextMenu?.(row.file_path, row.is_locked, groupNumber, e.clientX, e.clientY, col);
  }

  return (
    <div
      data-testid={rowFileTestid(groupId, row.basename)}
      data-density={density}
      className={cn(
        // The separator INSIDE a group is `row-line` (#f4eee3), deliberately
        // lighter than the group rule: inside a group the rows are alternatives
        // to each other and want to read as one block, and the group boundary
        // is the structural line (layout REPLY L1). `hairline-soft`, which this
        // wore before, is a full step darker and made every row look like its
        // own boundary.
        "flex items-center border-b border-row-line bg-panel text-ink hover:bg-subtle cursor-pointer",
        // Box geometry, per density (REPLY §"Row heights"). `items-center`, not
        // `items-start` — R4, and «the single biggest reason the shipped row
        // reads as a spreadsheet rather than a photo row». The height is the
        // TOTAL including the 1px bottom border, because that is the number
        // the virtualiser estimates with (lib/rowMetrics.ts).
        isLastInGroup ? metrics.rowLast : metrics.row,
        row.is_ref_winner && "bg-toolbar hover:bg-subtle",
        // Keeper cue, half 2 of 2 (copy audit R7 / design Q2, 2026-09-18):
        // a 2px accent strip on the row's left edge replaces the "Ref" pill
        // that used to sit beside the filename and duplicated the Similarity
        // column's ★ Ref badge. Every row reserves the 2px (transparent) so
        // the keeper's strip costs no horizontal shift. The token is the
        // same `sim-ref` ink slice (a) gave the badge — one accent, two
        // channels (position/weight vs. badge), both surviving grayscale.
        // …and the strip is the ACCENT (Q2), not the badge's own ink: at
        // #a85f2e it sat 5/255 from the group strip's #a85a2c directly above
        // it in the same vertical line, which reads as a rendering fault
        // rather than as two different things.
        "border-l-2 border-l-transparent",
        row.is_ref_winner && "border-l-warm",
        // Delete wash wins over the keeper tint; selection wins over both
        // (tailwind-merge resolves each bg conflict in favour of the later
        // class, so this order IS the precedence).
        isDeleting && "bg-delete-row hover:bg-delete-row",
        // Selection is a TINT, full stop (Q7). It used to add a 1px accent
        // ring as well; once the keyboard cursor draws a 2px accent outline
        // the two sat a pixel apart in the same hue, so on a multi-selection
        // the ▸ caret was the only thing still saying where the cursor was.
        // Fill = state, stroke = cursor — one stroke on this screen.
        isSelected && "bg-select text-select-ink hover:bg-select",
        // Closes the group frame under its last child (#878).
        isLastInGroup && "border-b-group-line"
      )}
      aria-selected={isSelected}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
    >
      {/* Thumbnail — 48/36px, 1px hairline border, and the warm diagonal hatch
          underneath (REPLY §"Thumbnail (R6)"). The hatch shows before the lazy
          image decodes and stays if it never does: «it reads as "frame awaiting
          an image" rather than "broken"». No per-row spinner — «at 40,000 rows
          spinners are noise». */}
      <div
        data-thumb=""
        className={cn(
          "thumb-hatch flex flex-shrink-0 items-center justify-center overflow-hidden border border-hairline",
          metrics.thumb
        )}
      >
        {thumbFailed ? (
          // Video without a usable thumbnail keeps the hatch and gains the
          // hairline ▷. Decorative: the row's name cell already says what the
          // file is, so a screen reader reading "play" here would be noise.
          row.media_type === "video" && (
            <span
              aria-hidden="true"
              data-thumb-fallback="video"
              className="text-[12px] leading-none text-ink-hairline"
            >
              ▷
            </span>
          )
        ) : (
          <img
            loading="lazy"
            src={row.thumbnail_url}
            width={metrics.thumbWidth}
            height={metrics.thumbWidth}
            alt={row.basename}
            onError={() => setThumbFailed(true)}
            className="object-cover w-full h-full"
          />
        )}
      </div>

      {/* Name + folder */}
      {/* Name + folder — the FILL column (L3). It takes whatever the shed
          columns freed, which is the point of shedding: the freed width belongs
          to the one string the user actually reads, not to a gutter on the
          right. Floor = its own stored width, then the table scrolls. */}
      <div data-col="name" className="overflow-hidden" style={columnCellStyle(COLUMN_BY_ID.name, columnWidths.name)}>
        <div className="flex items-center gap-1 flex-wrap">
          <span
            className={cn(
              "text-sm truncate",
              // Keeper cue, half 1 of 2: the keeper's name is the only one at
              // weight 600. Every row used to be semibold, which is what made
              // the dropped "Ref" pill the row's only left-hand keeper signal.
              row.is_ref_winner ? "font-semibold" : "font-medium",
              isDeleting && "line-through text-danger-warm"
            )}
          >
            {row.basename}
          </span>
        </div>
        {/* Folder sub-line — 11px mono, truncated from the LEFT (REPLY L2:
            «truncate from the left (`direction: rtl` + `text-align: left`), so
            the leaf stays visible»). A path elided at its END hides the one
            segment that distinguishes two copies of the same file. The inner
            `dir="ltr"` isolate is what stops the bidi algorithm reordering the
            separators inside an otherwise-RTL box. */}
        <div
          dir="rtl"
          data-col-folder=""
          className="truncate text-left font-mono text-[11px] text-ink-muted"
          title={row.folder}
        >
          <bdi dir="ltr">{row.folder}</bdi>
        </div>
      </div>

      {/* Similarity */}
      <div data-col="similarity" className="flex-shrink-0 text-sm overflow-hidden" style={{ width: columnWidths.similarity }}>
        <span
          data-sim-state={similarityBadgeState(row.similarity)}
          // Q1 — the classification's new home now that its column is gone.
          title={classification}
          className={cn(
            // REPLY §"Similarity badge (R13)": padding 2px 8px, radius 5px,
            // 11px text, gap 5px, 1px border. The shipped 6/2 pad + r4 + 12px
            // + gap 4 drifted from the settled badge vocabulary; the weight and
            // border STYLE stay per-state (they are the grayscale cue).
            "inline-flex items-center gap-[5px] rounded-[5px] border px-2 py-[2px] text-[11px]",
            similarityBadgeBorderClass(badge.border),
            badge.weight,
            badge.colors
          )}
        >
          {badge.prefixGlyph && (
            // Decorative: the state is already carried by the label text plus
            // the border style, and a screen reader reading "star Ref" would
            // be noise. Kept OUT of the label span so the cell's queryable
            // text stays exactly `similarityLabel()`'s output.
            <span aria-hidden="true" className="text-[12px] leading-none">{badge.glyph}</span>
          )}
          <span>{simLabel}</span>
        </span>
      </div>

      {/* Action — the DECISION (open questions Q1). This column used to print
          the scanner's classification while the real decision control sat
          unlabelled to its right, which made the impostor the most confusable
          thing on the row. "Action" now names the thing the user acts on, and
          the control has a real column with a real width instead of the
          header's hardcoded alignment spacer. The `data-col="action"` value is
          unchanged, so #735's right-click pre-fill still opens the Set Action
          dialog on its "Action" field — and that field's seed
          (`row.user_decision`) finally matches what the cell shows. */}
      <div data-col="action" className="flex-shrink-0 overflow-hidden" style={{ width: columnWidths.action }}>
        <DecisionControl
          value={row.user_decision}
          onChange={(val) => onDecision(row.file_path, val)}
          disabled={row.is_locked}
          density={density}
          data-testid={rowDecisionTestid(groupId, row.basename)}
        />
      </div>

      {/* Score — the "keep" label riding above the number, then the Daylight
          mini bar (#878 / REPLY §"Score bar (R15)"). The label stays because
          «without it a bare bar and a number in a 96px cell is an unlabelled
          quantity, and this number is the one the "keep best" button acts on»;
          compact drops it and keeps the number. The bar is aria-hidden and
          contributes NO text. The NUMBER carries `data-score-value` so the
          scenarios that used to read the whole cell (when the number was its
          only content) keep an exact handle now that a label shares the box. */}
      <div
        data-col="score"
        data-col-compact={scoreCompact ? "" : undefined}
        className={cn("flex-shrink-0 text-ink-muted overflow-hidden", cellTypeClass("score"))}
        style={{ width: scoreCompact ? SCORE_COMPACT_WIDTH : columnWidths.score }}
      >
        <div
          className={cn(
            "flex items-baseline mb-[4px]",
            metrics.showScoreLabel && !scoreCompact
              ? "justify-between"
              : "justify-end"
          )}
        >
          {metrics.showScoreLabel && !scoreCompact && (
            <span data-score-label="" className="font-sans text-[10px] font-medium leading-none">
              {t("web.score.keep_label", "keep")}
            </span>
          )}
          <span data-score-value="" className="text-[11px] font-semibold leading-none">
            {formatScore(row.score)}
          </span>
        </div>
        {row.score !== null && (
          <div
            aria-hidden="true"
            data-score-track=""
            className="h-[6px] w-full rounded-[3px] bg-score-track overflow-hidden"
          >
            <div
              data-score-fill=""
              // SOLID, not a gradient (REPLY): «at 96px a two-stop gradient is
              // three or four distinguishable pixels of variation, which is
              // invisible at best and a banding artefact at worst».
              className="h-full rounded-[3px] bg-score-fill"
              style={{ width: scoreBarWidth(row.score) }}
            />
          </div>
        )}
      </div>

      {/* Dimensions — first to go when the table narrows (L3): resolution is
          the field most often identical across a duplicate group. */}
      {shows("dims") && (
        <div data-col="dims" className={cn("flex-shrink-0 text-xs text-ink-muted overflow-hidden", cellTypeClass("dims"))} style={{ width: columnWidths.dims }}>
          {formatDims(row.pixel_width, row.pixel_height)}
        </div>
      )}

      {/* File size */}
      <div data-col="size" className={cn("flex-shrink-0 text-xs text-ink-muted overflow-hidden", cellTypeClass("size"))} style={{ width: columnWidths.size }}>
        {formatBytes(row.file_size_bytes)}
      </div>

      {/* Shot date — shed after dims, because it is a primary sort (L3). */}
      {shows("date") && (
        <div data-col="date" className={cn("flex-shrink-0 text-xs text-ink-muted overflow-hidden", cellTypeClass("date"))} style={{ width: columnWidths.date }}>
          {formatDate(row.shot_date)}
        </div>
      )}

      {/* Lock toggle */}
      <div className="flex-shrink-0">
        <LockToggle
          checked={row.is_locked}
          onChange={(locked) => onLock(row.file_path, locked)}
          density={density}
          data-testid={rowLockTestid(groupId, row.basename)}
        />
      </div>
    </div>
  );
}
