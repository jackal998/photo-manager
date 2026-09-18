// File row inside a group: thumbnail + metadata columns + decision + lock.

import type { MouseEvent } from "react";
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
  type ColumnId,
} from "@/lib/resultColumns";
import { scoreBarWidth } from "@/lib/scoreBar";
import {
  SIMILARITY_BADGE,
  similarityBadgeBorderClass,
  similarityBadgeState,
} from "@/lib/similarityBadge";
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
  /** The <940px score-cell collapse (see `isScoreCompact`). */
  scoreCompact?: boolean;
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

export function FileRow({ row, groupId, groupNumber, columnWidths, visibleCols, scoreCompact = false, onDecision, onLock, onSelect, onOpenFullRes, onContextMenu, isSelected, isLastInGroup }: FileRowProps) {
  const t = useT();
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
      className={cn(
        // The separator INSIDE a group is `row-line` (#f4eee3), deliberately
        // lighter than the group rule: inside a group the rows are alternatives
        // to each other and want to read as one block, and the group boundary
        // is the structural line (layout REPLY L1). `hairline-soft`, which this
        // wore before, is a full step darker and made every row look like its
        // own boundary.
        "flex items-start gap-3 px-4 py-2 border-b border-row-line bg-panel text-ink hover:bg-subtle cursor-pointer",
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
      {/* Thumbnail */}
      <div className="flex-shrink-0 w-16 h-16 bg-subtle rounded overflow-hidden flex items-center justify-center">
        <img
          loading="lazy"
          src={row.thumbnail_url}
          width={64}
          height={64}
          alt={row.basename}
          className="object-cover w-full h-full"
        />
      </div>

      {/* Name + folder */}
      {/* Name + folder. Fixed and resizable, NOT the L3 budget's flex-to-fill
          column — see the note in lib/resultColumns.ts: a grow column's box
          ignores its own resize handle while any slack is left, measured. */}
      <div data-col="name" className="flex-shrink-0 min-w-0 overflow-hidden" style={{ width: columnWidths.name }}>
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
        <div className="text-xs text-ink-muted truncate" title={row.folder}>
          {row.folder}
        </div>
      </div>

      {/* Similarity */}
      <div data-col="similarity" className="flex-shrink-0 text-sm overflow-hidden" style={{ width: columnWidths.similarity }}>
        <span
          data-sim-state={similarityBadgeState(row.similarity)}
          // Q1 — the classification's new home now that its column is gone.
          title={classification}
          className={cn(
            "inline-flex items-center gap-1 text-xs rounded px-1.5 py-0.5 border",
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
            <span aria-hidden="true">{badge.glyph}</span>
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
          data-testid={rowDecisionTestid(groupId, row.basename)}
        />
      </div>

      {/* Score — number (unchanged text) plus the Daylight mini bar (#878).
          The bar is aria-hidden and contributes NO text, so the cell's
          queryable content stays exactly `formatScore()`'s output, which is
          what the parity counter and every scenario read. Unscored rows
          (score === null) get the em dash and no track, matching the Qt
          delegate this ports. */}
      <div
        data-col="score"
        data-col-compact={scoreCompact ? "" : undefined}
        className={cn("flex-shrink-0 text-xs text-ink-muted overflow-hidden", cellTypeClass("score"))}
        style={{ width: scoreCompact ? SCORE_COMPACT_WIDTH : columnWidths.score }}
      >
        <div>{formatScore(row.score)}</div>
        {row.score !== null && (
          <div
            aria-hidden="true"
            data-score-track=""
            className="mt-0.5 h-1 w-full rounded-full bg-score-track overflow-hidden"
          >
            <div
              data-score-fill=""
              className="h-full rounded-full bg-linear-to-r from-score-fill to-score-fill-end"
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
      <div className="flex-shrink-0 pt-1">
        <LockToggle
          checked={row.is_locked}
          onChange={(locked) => onLock(row.file_path, locked)}
          data-testid={rowLockTestid(groupId, row.basename)}
        />
      </div>
    </div>
  );
}
