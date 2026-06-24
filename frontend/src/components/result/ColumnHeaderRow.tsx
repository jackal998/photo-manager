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
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/useT";
import { COLUMNS, type ColumnId, type SortDirection } from "@/lib/resultColumns";
import { colHeaderTestid, colResizeTestid } from "@/testids";

interface ColumnHeaderRowProps {
  columnWidths: Record<ColumnId, number>;
  sortColumn: ColumnId | null;
  sortDirection: SortDirection;
  onToggleSort: (column: ColumnId) => void;
  /** Live-updates the width per move (persist=false) and commits on mouseup
   *  (persist=true) — see ColumnHeaderRow.handleResizeStart. */
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

  // Begin a resize drag: track the pointer on the window so the drag keeps
  // working even when the cursor leaves the 6px handle. Each move updates the
  // width in-memory only (persist=false) for live feedback; the final width is
  // persisted once on mouseup (persist=true) to avoid a localStorage write per
  // mousemove.
  function handleResizeStart(e: ReactMouseEvent, column: ColumnId) {
    e.preventDefault();
    // Stop the mousedown from bubbling to the header cell's sort onClick.
    e.stopPropagation();
    const startX = e.clientX;
    const startWidth = columnWidths[column];
    let latest = startWidth;
    function onMove(ev: globalThis.MouseEvent) {
      latest = startWidth + (ev.clientX - startX);
      onResize(column, latest, false);
    }
    function onUp() {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      onResize(column, latest, true); // commit the final width to localStorage
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
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
