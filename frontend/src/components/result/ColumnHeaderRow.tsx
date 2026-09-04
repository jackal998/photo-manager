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

import type { MouseEvent as ReactMouseEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/useT";
import { COLUMNS, type ColumnId, type SortDirection } from "@/lib/resultColumns";
import { colHeaderTestid, colResizeTestid } from "@/testids";

interface ColumnHeaderRowProps {
  columnWidths: Record<ColumnId, number>;
  sortColumn: ColumnId | null;
  sortDirection: SortDirection;
  onToggleSort: (column: ColumnId) => void;
  /** Live-updates the width per move (persist=false) and commits once at the
   *  end of the drag (persist=true) — see ColumnHeaderRow's drag useEffect. */
  onResize: (column: ColumnId, width: number, persist?: boolean) => void;
}

export function ColumnHeaderRow({
  columnWidths,
  sortColumn,
  sortDirection,
  onToggleSort,
  onResize,
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

  function handleResizeStart(e: ReactMouseEvent, column: ColumnId) {
    e.preventDefault();
    // Stop the mousedown from bubbling to the header cell's sort onClick.
    e.stopPropagation();
    setDrag({ column, startX: e.clientX, startWidth: columnWidths[column] });
  }

  return (
    <div
      className="sticky top-0 z-10 flex items-center gap-3 px-4 py-1 bg-neutral-100 border-b border-neutral-200 text-xs font-semibold text-neutral-600 select-none"
    >
      {/* Thumbnail spacer — aligns header cells with FileRow's metadata cells. */}
      <div className="flex-shrink-0 w-16" aria-hidden="true" />

      {COLUMNS.map((col) => {
        const isActive = sortColumn === col.id;
        const indicator = isActive
          ? sortDirection === "asc"
            ? " ▲"
            : " ▼"
          : "";
        const label = t(col.labelKey, col.labelFallback);
        return (
          <div
            key={col.id}
            data-testid={colHeaderTestid(col.id)}
            // relative so the resize handle can anchor to the cell's right edge.
            className={cn(
              "relative flex-shrink-0 flex items-center overflow-hidden",
              col.align === "right" ? "justify-end" : "justify-start",
              col.sortable && "cursor-pointer hover:text-neutral-900"
            )}
            style={{ width: columnWidths[col.id] }}
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
            <span className="truncate">
              {label}
              {indicator}
            </span>
            {/* Resize handle — right-edge grab strip. */}
            <span
              data-testid={colResizeTestid(col.id)}
              role="separator"
              aria-orientation="vertical"
              className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-neutral-400/60"
              onMouseDown={(e) => handleResizeStart(e, col.id)}
              onClick={(e) => e.stopPropagation()}
            />
          </div>
        );
      })}
    </div>
  );
}
