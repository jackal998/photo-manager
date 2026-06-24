// Unit tests for the result-tree sticky column header (#685): sort clicks +
// resize-drag wiring. Rendered directly with explicit props + spy callbacks.

import type { ComponentProps } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { ColumnHeaderRow } from "./ColumnHeaderRow";
import { DEFAULT_COLUMN_WIDTHS } from "@/lib/resultColumns";
import { colHeaderTestid, colResizeTestid } from "@/testids";

function renderHeader(
  overrides: Partial<ComponentProps<typeof ColumnHeaderRow>> = {}
) {
  const onToggleSort = vi.fn();
  const onResize = vi.fn();
  render(
    <ColumnHeaderRow
      columnWidths={DEFAULT_COLUMN_WIDTHS}
      sortColumn={null}
      sortDirection="asc"
      onToggleSort={onToggleSort}
      onResize={onResize}
      {...overrides}
    />
  );
  return { onToggleSort, onResize };
}

describe("ColumnHeaderRow", () => {
  it("renders a header cell for every metadata column", () => {
    renderHeader();
    for (const id of ["name", "similarity", "action", "score", "dims", "size", "date"]) {
      expect(screen.getByTestId(colHeaderTestid(id))).toBeInTheDocument();
    }
  });

  it("clicking a sortable header (File Name) calls onToggleSort", () => {
    const { onToggleSort } = renderHeader();
    fireEvent.click(screen.getByTestId(colHeaderTestid("name")));
    expect(onToggleSort).toHaveBeenCalledWith("name");
  });

  it("clicking the Size header calls onToggleSort('size')", () => {
    const { onToggleSort } = renderHeader();
    fireEvent.click(screen.getByTestId(colHeaderTestid("size")));
    expect(onToggleSort).toHaveBeenCalledWith("size");
  });

  it("clicking a non-sortable header (Similarity) does NOT sort", () => {
    const { onToggleSort } = renderHeader();
    fireEvent.click(screen.getByTestId(colHeaderTestid("similarity")));
    expect(onToggleSort).not.toHaveBeenCalled();
  });

  it("shows the sort-direction indicator on the active column", () => {
    renderHeader({ sortColumn: "name", sortDirection: "asc" });
    expect(screen.getByTestId(colHeaderTestid("name"))).toHaveTextContent("▲");
  });

  it("a resize drag reports the new width via onResize (live move + commit)", () => {
    const { onResize, onToggleSort } = renderHeader();
    const handle = screen.getByTestId(colResizeTestid("name"));
    const target = DEFAULT_COLUMN_WIDTHS.name + 60;
    // name default width = 160; mousedown at x=200, drag to x=260 → +60 → 220.
    fireEvent.mouseDown(handle, { clientX: 200 });
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 260 }));
    });
    // Live move updates in-memory only (persist=false).
    expect(onResize).toHaveBeenCalledWith("name", target, false);
    act(() => {
      window.dispatchEvent(new MouseEvent("mouseup", { clientX: 260 }));
    });
    // mouseup commits the final width to localStorage (persist=true).
    expect(onResize).toHaveBeenCalledWith("name", target, true);
    // Grabbing the resize handle must not also trigger a sort.
    expect(onToggleSort).not.toHaveBeenCalled();
  });

  it("stops tracking the drag after mouseup", () => {
    const { onResize } = renderHeader();
    const handle = screen.getByTestId(colResizeTestid("name"));
    fireEvent.mouseDown(handle, { clientX: 100 });
    act(() => {
      window.dispatchEvent(new MouseEvent("mouseup", { clientX: 100 }));
    });
    onResize.mockClear();
    // A move AFTER mouseup must not fire onResize (listener removed).
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 400 }));
    });
    expect(onResize).not.toHaveBeenCalled();
  });
});
