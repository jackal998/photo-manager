// Unit tests for the result-tree sticky column header (#685): sort clicks +
// resize-drag wiring. Rendered directly with explicit props + spy callbacks.

import type { ComponentProps } from "react";
import { useCallback, useState } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";

import { ColumnHeaderRow } from "./ColumnHeaderRow";
import { DEFAULT_COLUMN_WIDTHS, MIN_COLUMN_WIDTH, type ColumnId } from "@/lib/resultColumns";
import { colHeaderTestid, colResizeTestid } from "@/testids";

function renderHeader(
  overrides: Partial<ComponentProps<typeof ColumnHeaderRow>> = {}
) {
  const onToggleSort = vi.fn();
  const onResize = vi.fn();
  const { unmount } = render(
    <ColumnHeaderRow
      columnWidths={DEFAULT_COLUMN_WIDTHS}
      sortColumn={null}
      sortDirection="asc"
      onToggleSort={onToggleSort}
      onResize={onResize}
      {...overrides}
    />
  );
  return { onToggleSort, onResize, unmount };
}

/**
 * Renders the header with the widths held in React state and `onResize` wired
 * the way ResultTree wires it (store.setColumnWidth: clamp to
 * MIN_COLUMN_WIDTH, update state on every move, persist only when
 * persist=true). Lets a test assert the RENDERED column width instead of only
 * a spy call — "the column keeps resizing after the button was released" is a
 * width bug, and the callback identity is stable here exactly as the store
 * action is in the app.
 */
function renderStatefulHeader() {
  const persisted: number[] = [];
  function Harness() {
    const [widths, setWidths] = useState(DEFAULT_COLUMN_WIDTHS);
    const onResize = useCallback(
      (column: ColumnId, width: number, persist?: boolean) => {
        const clamped = Math.max(MIN_COLUMN_WIDTH, Math.round(width));
        setWidths((prev) => ({ ...prev, [column]: clamped }));
        if (persist) persisted.push(clamped);
      },
      []
    );
    return (
      <ColumnHeaderRow
        columnWidths={widths}
        sortColumn={null}
        sortDirection="asc"
        onToggleSort={vi.fn()}
        onResize={onResize}
      />
    );
  }
  render(<Harness />);
  return {
    /** Every persist=true width, in order — the #685 contract allows exactly one per drag. */
    persisted,
    nameWidth: () => screen.getByTestId(colHeaderTestid("name")).style.width,
  };
}

// Window event types the resize drag installs; a drag that ends must remove
// every one of them with the same function reference it added.
const DRAG_EVENT_TYPES = ["mousemove", "mouseup", "blur", "pointercancel"];

/** Spies on window add/removeEventListener. Install BEFORE rendering. */
function spyOnWindowListeners() {
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  return {
    expectNoLeakedDragListeners() {
      const added = add.mock.calls.filter(([type]) =>
        DRAG_EVENT_TYPES.includes(type as string)
      );
      // Guards the guard: if the component stopped installing window listeners
      // altogether, "nothing leaked" would be vacuously true.
      expect(added.length).toBeGreaterThan(0);
      const leaked = added.filter(
        ([type, fn]) =>
          !remove.mock.calls.some(([t, f]) => t === type && f === fn)
      );
      expect(leaked.map(([type]) => type)).toEqual([]);
    },
  };
}

describe("ColumnHeaderRow", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

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
      // buttons=1 — a real in-drag move always reports the held button; a move
      // with buttons=0 means the button was released unseen (see the #796 test
      // below), so the fixture has to say which one it is.
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 260, buttons: 1 }));
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

  it("removes the window drag listeners when unmounted mid-drag (#796)", () => {
    const { onResize, unmount } = renderHeader();
    const handle = screen.getByTestId(colResizeTestid("name"));
    // Start a drag, then unmount before releasing the mouse.
    fireEvent.mouseDown(handle, { clientX: 100 });
    unmount();
    onResize.mockClear();
    // A move after unmount must not reach a leaked listener (the pre-#796 code
    // added the window listeners imperatively with no unmount cleanup).
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 400 }));
    });
    expect(onResize).not.toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------
  // #796 — the drag must also end when the mouse is released somewhere the
  // window never hears about it (over another application). Before this, the
  // window mousemove/mouseup listeners stayed attached and the next move over
  // the page kept resizing the column with no button held.
  // ---------------------------------------------------------------------

  const DRAGGED_WIDTH = DEFAULT_COLUMN_WIDTHS.name + 60;

  /** mousedown on the name handle at x=200, then drag to x=260 (+60px). */
  function startDragAndMove() {
    const handle = screen.getByTestId(colResizeTestid("name"));
    fireEvent.mouseDown(handle, { clientX: 200 });
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 260, buttons: 1 }));
    });
  }

  it("ends the drag when the window loses focus mid-drag, leaking no listener (#796)", () => {
    const listeners = spyOnWindowListeners();
    const { persisted, nameWidth } = renderStatefulHeader();
    startDragAndMove();
    expect(nameWidth()).toBe(`${DRAGGED_WIDTH}px`);

    // The button is released over another application: the window gets a blur,
    // never a mouseup.
    act(() => {
      window.dispatchEvent(new Event("blur"));
    });
    listeners.expectNoLeakedDragListeners();

    // Moving back over the page must not keep resizing the column.
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 500, buttons: 0 }));
    });
    expect(nameWidth()).toBe(`${DRAGGED_WIDTH}px`);
    // Exactly one persisted write for the whole drag (#685 contract).
    expect(persisted).toEqual([DRAGGED_WIDTH]);
  });

  it("ends the drag on pointercancel mid-drag, leaking no listener (#796)", () => {
    const listeners = spyOnWindowListeners();
    const { persisted, nameWidth } = renderStatefulHeader();
    startDragAndMove();

    // A system-level gesture (touch/pen cancel, native drag) cancels the
    // pointer without ever delivering a mouseup.
    act(() => {
      window.dispatchEvent(new Event("pointercancel"));
    });
    listeners.expectNoLeakedDragListeners();

    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 500, buttons: 0 }));
    });
    expect(nameWidth()).toBe(`${DRAGGED_WIDTH}px`);
    expect(persisted).toEqual([DRAGGED_WIDTH]);
  });

  it("ends the drag when a move arrives with the button already released (#796)", () => {
    const listeners = spyOnWindowListeners();
    const { persisted, nameWidth } = renderStatefulHeader();
    startDragAndMove();

    // The issue's literal repro: released outside the viewport over something
    // that took no focus, so neither mouseup nor blur ever arrives — the only
    // signal is that the next move carries no held button.
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 500, buttons: 0 }));
    });
    listeners.expectNoLeakedDragListeners();
    expect(nameWidth()).toBe(`${DRAGGED_WIDTH}px`);

    // ...and it stays put for every later move.
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 700, buttons: 0 }));
    });
    expect(nameWidth()).toBe(`${DRAGGED_WIDTH}px`);
    expect(persisted).toEqual([DRAGGED_WIDTH]);
  });
});
