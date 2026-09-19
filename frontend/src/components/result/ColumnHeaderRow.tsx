// Sticky column-header bar for the result tree (#685 → s45/s47).
//
// Renders one header cell per metadata column (COLUMNS registry), aligned to
// the FileRow body cells by sharing the same per-column widths + the same
// leading thumbnail spacer and flex gap/padding. Sortable columns (File Name,
// Size) toggle the sort on click; every column has a right-edge resize handle.
//
// Kept INSIDE the result-tree scroll container (sticky top-0) so it scrolls
// horizontally with the body when columns are widened past the viewport while
// staying pinned to the top vertically.

import type { MouseEvent as ReactMouseEvent, Ref } from "react";
import { useEffect, useRef, useState } from "react";
import { Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/useT";
import {
  COLUMNS,
  SCORE_COMPACT_WIDTH,
  columnCellStyle,
  type ColumnId,
  type SortDirection,
} from "@/lib/resultColumns";
import { colHeaderTestid, colResizeTestid, RESULT_COL_HEADER_ROW } from "@/testids";

interface ColumnHeaderRowProps {
  columnWidths: Record<ColumnId, number>;
  /** Columns the table width currently affords (ResultTree computes it from
   *  `visibleColumns`). Omitted = render every column — FileRow-style unit
   *  tests mount the header with no measured width. */
  visibleCols?: ReadonlySet<ColumnId>;
  /** The <940px score-cell collapse (see `isScoreCompact`). */
  scoreCompact?: boolean;
  sortColumn: ColumnId | null;
  sortDirection: SortDirection;
  onToggleSort: (column: ColumnId) => void;
  /** Live-updates the width per move (persist=false) and commits once at the
   *  end of the drag (persist=true) — see ColumnHeaderRow's drag useEffect. */
  onResize: (column: ColumnId, width: number, persist?: boolean) => void;
  /** Root-element ref (React 19 ref-as-prop). ResultTree measures this row's
   *  height and hands it to the virtualizer as `scrollMargin` (#699) — the
   *  header occupies the first N px of the scroll container's content, so the
   *  row list does not start at scrollTop 0. */
  ref?: Ref<HTMLDivElement>;
}

export function ColumnHeaderRow({
  columnWidths,
  visibleCols,
  scoreCompact = false,
  sortColumn,
  sortDirection,
  onToggleSort,
  onResize,
  ref,
}: ColumnHeaderRowProps) {
  const t = useT();

  // Active resize drag, or null. Driving the window listeners from a useEffect
  // (keyed on this state) instead of adding them imperatively in the mousedown
  // handler means React removes them on unmount too — not only on mouseup — so
  // unmounting the tree mid-drag no longer leaks a window mousemove/mouseup
  // listener holding a stale onResize closure (#796). The drag also ends on
  // `blur` / `pointercancel` / a move with the button already released, so a
  // mouse-up the window never receives cannot leave the drag armed (#796).
  const [drag, setDrag] = useState<{
    column: ColumnId;
    startX: number;
    startWidth: number;
  } | null>(null);
  const latestWidthRef = useRef(0);

  useEffect(() => {
    if (drag === null) return;
    const { column, startX, startWidth } = drag;
    latestWidthRef.current = startWidth;
    // Commit the final width to localStorage once (persist=true) — avoids a
    // localStorage write per mousemove — then end the drag. Shared by every
    // end-of-drag trigger below so all of them keep the #685 contract of
    // exactly ONE persisted write per drag.
    function endDrag() {
      onResize(column, latestWidthRef.current, true);
      setDrag(null);
    }
    // Track the pointer on the window so the drag keeps working when the cursor
    // leaves the 6px handle. Each move updates the width in-memory only
    // (persist=false) for live feedback.
    function onMove(ev: globalThis.MouseEvent) {
      // The button is already up: it was released somewhere we never heard
      // about — outside the window, with no focus change, so neither `mouseup`
      // nor `blur` reached us (#796). Without this the next move over the page
      // would keep resizing the column with no button held.
      if (ev.buttons === 0) {
        endDrag();
        return;
      }
      latestWidthRef.current = startWidth + (ev.clientX - startX);
      onResize(column, latestWidthRef.current, false);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", endDrag);
    // A release outside the window usually takes focus with it, and a
    // system-level gesture (touch/pen cancel, native drag) cancels the pointer
    // without a mouseup — end the drag on both rather than leaving the window
    // listeners live (#796).
    window.addEventListener("blur", endDrag);
    window.addEventListener("pointercancel", endDrag);
    // Unconditional removal: the cleanup runs on every drag-state change AND on
    // unmount, so no path can leave a window listener behind.
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", endDrag);
      window.removeEventListener("blur", endDrag);
      window.removeEventListener("pointercancel", endDrag);
    };
  }, [drag, onResize]);

  // The shed set, or the whole registry when the table width is unmeasured.
  const cols = visibleCols
    ? COLUMNS.filter((c) => visibleCols.has(c.id))
    : COLUMNS;

  function handleResizeStart(e: ReactMouseEvent, column: ColumnId) {
    e.preventDefault();
    // Stop the mousedown from bubbling to the header cell's sort onClick.
    e.stopPropagation();
    setDrag({ column, startX: e.clientX, startWidth: columnWidths[column] });
  }

  return (
    <div
      ref={ref}
      data-testid={RESULT_COL_HEADER_ROW}
      // 28px including the bottom rule (box-border is Tailwind's global default),
      // warm chrome one step off the panel, 11px/600 uppercase with .06em
      // tracking — layout REPLY L1 "Column header". Uppercase + tracking at
      // 11px is what separates a header from a bold data row at a glance; the
      // sentence-case `text-xs font-semibold` it replaces read as the first row
      // of data (audit C1). The height is MEASURED by ResultTree and handed to
      // the virtualizer as `scrollMargin` (#699) — it is never a constant there.
      className="sticky top-0 z-10 flex h-7 items-center gap-3 px-4 bg-toolbar border-b border-group-line text-[11px] font-semibold uppercase tracking-[0.06em] leading-none text-ink-muted select-none"
    >
      {/* Thumbnail spacer — aligns header cells with FileRow's metadata cells. */}
      <div className="flex-shrink-0 w-16" aria-hidden="true" />

      {cols.map((col) => {
        const isActive = sortColumn === col.id;
        const label = t(col.labelKey, col.labelFallback);
        const isDragging = drag?.column === col.id;
        const width =
          col.id === "score" && scoreCompact
            ? SCORE_COMPACT_WIDTH
            : columnWidths[col.id];
        return (
          <div
            key={col.id}
            data-testid={colHeaderTestid(col.id)}
            data-col-compact={col.id === "score" && scoreCompact ? "" : undefined}
            // relative so the resize handle can anchor to the cell's right edge.
            // `data-col-basis` is the width the STORE holds. The flexible
            // column renders WIDER than that (it fills what the shed columns
            // freed), so s47 compares the persisted number against this rather
            // than against the box — the two stopped being the same thing when
            // File Name became the fill column.
            data-col-basis={columnWidths[col.id]}
            className={cn(
              "relative flex items-center gap-1.5 overflow-hidden",
              !col.flexible && "flex-shrink-0",
              col.align === "right" ? "justify-end" : "justify-start",
              // `group/sort` drives the hover-only ▾ below without a second
              // state — the affordance appears where the pointer already is.
              col.sortable && "group/sort cursor-pointer hover:text-ink",
              isActive && "text-ink"
            )}
            style={columnCellStyle(col, width)}
            role={col.sortable ? "button" : undefined}
            aria-sort={
              col.sortable
                ? isActive
                  ? sortDirection === "asc"
                    ? "ascending"
                    : "descending"
                  : "none"
                : undefined
            }
            onClick={col.sortable ? () => onToggleSort(col.id) : undefined}
          >
            <span className="truncate">{label}</span>
            {/* Sort indicator. Unsorted shows NO glyph at rest (the REPLY is
                explicit — a permanent ⇅ on every sortable header is noise) and
                a hairline ▾ on hover; sorted shows ▾/▴ in the accent with the
                label promoted to `ink`. aria-hidden because `aria-sort` on the
                cell is what a screen reader reads. */}
            {col.sortable && (
              <span
                aria-hidden="true"
                data-sort-indicator={isActive ? "active" : "hover"}
                className={cn(
                  "flex-shrink-0 text-[9px] leading-none",
                  isActive
                    ? "text-warm"
                    : "text-ink-hairline opacity-0 group-hover/sort:opacity-100"
                )}
              >
                {isActive ? (sortDirection === "asc" ? "▴" : "▾") : "▾"}
              </span>
            )}
            {/* Resize handle — an 8px grab strip on the column's right edge,
                invisible at rest. It stays INSIDE the cell (rather than
                straddling the boundary as the REPLY draws it) because the cell
                clips its own overflow to stop a long translated label spilling
                into the next column; a half-outside handle would be clipped in
                half. Hover paints a 2px hairline rule inset 6px top and bottom;
                dragging paints it in the accent at full height and drops a 1px
                guide down the body. Double-click restores the default width. */}
            <span
              data-testid={colResizeTestid(col.id)}
              role="separator"
              aria-orientation="vertical"
              data-resizing={isDragging ? "" : undefined}
              className="group/resize absolute right-0 top-0 z-10 h-full w-2 cursor-col-resize"
              onMouseDown={(e) => handleResizeStart(e, col.id)}
              onClick={(e) => e.stopPropagation()}
              onDoubleClick={(e) => {
                e.stopPropagation();
                onResize(col.id, col.defaultWidth, true);
              }}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "pointer-events-none absolute right-0 w-0.5",
                  isDragging
                    ? "inset-y-0 bg-warm"
                    : "inset-y-1.5 bg-ink-hairline opacity-0 group-hover/resize:opacity-100"
                )}
              />
              {isDragging && (
                <span
                  aria-hidden="true"
                  data-resize-guide=""
                  className="pointer-events-none absolute right-0 top-full h-screen w-px bg-group-line"
                />
              )}
            </span>
          </div>
        );
      })}

      {/* Row-chrome header (#878 slice b). The lock padlock is NOT an entry in
          COLUMNS — not sortable, not resizable, no persisted width — so it gets
          no resize handle and no aria-sort. The design asks for a padlock GLYPH
          over that column rather than the word; the accessible name stays the
          translated "Lock" (web.column.lock), carried in sr-only text so a
          screen reader and the i18n passthrough probe both still see it.

          The hardcoded decision SPACER that used to sit here — the control's
          measured intrinsic width, parked here purely to keep the padlock glyph
          over the padlocks — is GONE: the decision control now lives in the
          `action` column with a real width, which is exactly what that spacer's
          comment asked for. */}
      <div
        data-col-chrome="lock"
        className="flex-shrink-0 w-4 flex items-center justify-center"
        title={t("web.column.lock", "Lock")}
      >
        <Lock className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="sr-only">{t("web.column.lock", "Lock")}</span>
      </div>
    </div>
  );
}
