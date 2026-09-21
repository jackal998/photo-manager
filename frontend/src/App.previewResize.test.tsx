// Preview-panel splitter drag (#739 handle, #851 end-of-drag) — the App-level
// counterpart of ColumnHeaderRow.test.tsx's #796 block.
//
// Rendered through the REAL <App /> and the REAL store, so the assertions are
// on the width the user actually sees (the inline width on the preview-pane
// wrapper) and on the real localStorage writes — not on a spy standing in for
// them. "The layout sticks to the cursor after an off-window release" is a
// width bug; a callback-only assertion would not have caught it.

import { type ReactElement } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from "vitest";

import App from "./App";
import { useAppStore } from "./store/useAppStore";
import { useI18nStore } from "./i18n/useI18nStore";
import { DEFAULT_PANEL_WIDTHS } from "./lib/panelWidths";
import { PREVIEW_PANE, PREVIEW_RESIZE_HANDLE } from "./testids";

// ---------------------------------------------------------------------------
// Stubs — App mounts the SSE subscription and boots i18n; neither may do real
// network I/O under jsdom (same stubs as App.test.tsx).
// ---------------------------------------------------------------------------

class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  url: string;
  constructor(url: string) {
    this.url = url;
  }
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  close = vi.fn();
}

const fetchStub = vi.fn(() => new Promise<Response>(() => {}));

beforeAll(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", fetchStub);
});

afterAll(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const START_WIDTH = DEFAULT_PANEL_WIDTHS.preview;
/** Drag the handle LEFT by this much — the handle sits left of the pane, so a
 *  negative x delta widens the preview (App.tsx's handlePreviewResizeStart). */
const DRAG_DX = 100;
const DRAGGED_WIDTH = START_WIDTH + DRAG_DX;
const GRAB_X = 600;

/** The rendered width of the preview column — the inline style App.tsx puts on
 *  the wrapper around <PreviewPane />. */
function previewWidthPx(): string {
  const wrapper = screen.getByTestId(PREVIEW_PANE).parentElement;
  if (wrapper === null) {
    throw new Error("preview pane has no wrapper element to read a width from");
  }
  return wrapper.style.width;
}

/** mousedown on the splitter at GRAB_X, then one held-button move DRAG_DX to
 *  the left. `buttons: 1` is what a real browser and Playwright send for an
 *  in-drag move; a move with `buttons: 0` means the button was already
 *  released (the #851 repro), so the fixture has to say which one it is. */
function startDragAndMove() {
  fireEvent.mouseDown(screen.getByTestId(PREVIEW_RESIZE_HANDLE), {
    clientX: GRAB_X,
  });
  act(() => {
    window.dispatchEvent(
      new MouseEvent("mousemove", { clientX: GRAB_X - DRAG_DX, buttons: 1 })
    );
  });
}

/** A move back over the page with nothing held, far enough left that a still
 *  live drag would visibly widen the pane again. */
function moveWithNoButtonHeld(clientX = 200) {
  act(() => {
    window.dispatchEvent(new MouseEvent("mousemove", { clientX, buttons: 0 }));
  });
}

// Window event types the splitter drag installs; a drag that ends must remove
// every one of them with the same function reference it added.
const DRAG_EVENT_TYPES = ["mousemove", "mouseup", "blur", "pointercancel"];

/**
 * Spies on window add/removeEventListener. Install AFTER render and before the
 * mousedown: App mounts other window listeners of its own (the unload guard,
 * the decision shortcuts), and those are not this drag's business — scoping the
 * spy to the gesture is what keeps the vacuity guard below honest.
 */
function spyOnWindowListeners() {
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  return {
    expectNoLeakedDragListeners() {
      const added = add.mock.calls.filter(([type]) =>
        DRAG_EVENT_TYPES.includes(type as string)
      );
      // Guards the guard: if the splitter stopped installing window listeners
      // altogether, "nothing leaked" would be vacuously true.
      expect(added.length).toBeGreaterThan(0);
      const leaked = added.filter(
        ([type, fn]) => !remove.mock.calls.some(([t, f]) => t === type && f === fn)
      );
      expect(leaked.map(([type]) => type)).toEqual([]);
    },
  };
}

/** Every width written to localStorage under "panelWidths", in order — the
 *  #739 contract allows exactly one per drag. */
let persistedWidths: number[] = [];

beforeEach(() => {
  localStorage.clear();
  persistedWidths = [];
  act(() => {
    useAppStore.getState().setPreviewWidth(START_WIDTH, false);
  });
  useI18nStore.setState({ locale: "en", catalog: {} });
  // Record the panelWidths writes while still performing them — the store
  // reads its own persisted blob back, so swallowing the write would change
  // the behaviour under test.
  const realSetItem = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
    this: Storage,
    key: string,
    value: string
  ) {
    if (key === "panelWidths") {
      persistedWidths.push((JSON.parse(value) as { preview: number }).preview);
    }
    realSetItem.call(this, key, value);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("App — preview-panel splitter drag", () => {
  it("widens the pane live during the drag and persists the width once on mouseup", () => {
    renderWithProviders(<App />);
    startDragAndMove();
    // Live move updates the rendered width in-memory only (persist=false).
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);
    expect(persistedWidths).toEqual([]);

    act(() => {
      window.dispatchEvent(new MouseEvent("mouseup", { clientX: GRAB_X - DRAG_DX }));
    });
    // mouseup commits the final width to localStorage exactly once.
    expect(persistedWidths).toEqual([DRAGGED_WIDTH]);
  });

  it("stops resizing after mouseup", () => {
    renderWithProviders(<App />);
    startDragAndMove();
    act(() => {
      window.dispatchEvent(new MouseEvent("mouseup", { clientX: GRAB_X - DRAG_DX }));
    });
    // A move after mouseup must not reach a listener (they were removed).
    act(() => {
      window.dispatchEvent(new MouseEvent("mousemove", { clientX: 200, buttons: 1 }));
    });
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);
  });

  // -----------------------------------------------------------------------
  // #851 — the drag must also end when the mouse is released somewhere the
  // window never hears about (over another application). Before this, the
  // window mousemove/mouseup listeners stayed attached and the next move over
  // the page kept resizing the preview pane with no button held, so the
  // layout stuck to the cursor until the user clicked again.
  // -----------------------------------------------------------------------

  it("ends the drag when the window loses focus mid-drag, leaking no listener (#851)", () => {
    renderWithProviders(<App />);
    const listeners = spyOnWindowListeners();
    startDragAndMove();
    // Control: the drag really did resize the pane, so the assertions below
    // cannot pass by the gesture having done nothing at all.
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);

    // The button is released over another application: the window gets a blur,
    // never a mouseup.
    act(() => {
      window.dispatchEvent(new Event("blur"));
    });
    listeners.expectNoLeakedDragListeners();

    // Moving back over the page must not keep resizing the pane.
    moveWithNoButtonHeld();
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);
    // Exactly one persisted write for the whole gesture (#739 contract).
    expect(persistedWidths).toEqual([DRAGGED_WIDTH]);
  });

  it("ends the drag on pointercancel mid-drag, leaking no listener (#851)", () => {
    renderWithProviders(<App />);
    const listeners = spyOnWindowListeners();
    startDragAndMove();
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);

    // A system-level gesture (touch/pen cancel, native drag) cancels the
    // pointer without ever delivering a mouseup.
    act(() => {
      window.dispatchEvent(new Event("pointercancel"));
    });
    listeners.expectNoLeakedDragListeners();

    moveWithNoButtonHeld();
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);
    expect(persistedWidths).toEqual([DRAGGED_WIDTH]);
  });

  it("ends the drag when a move arrives with the button already released (#851)", () => {
    renderWithProviders(<App />);
    const listeners = spyOnWindowListeners();
    startDragAndMove();
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);

    // The issue's literal repro: released outside the viewport over something
    // that took no focus, so neither mouseup nor blur ever arrives — the only
    // signal left is that the next move carries no held button.
    moveWithNoButtonHeld();
    listeners.expectNoLeakedDragListeners();
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);

    // ...and it stays put for every later move.
    moveWithNoButtonHeld(100);
    expect(previewWidthPx()).toBe(`${DRAGGED_WIDTH}px`);
    expect(persistedWidths).toEqual([DRAGGED_WIDTH]);
  });

  it("removes the window drag listeners when unmounted mid-drag (#851)", () => {
    const { unmount } = renderWithProviders(<App />);
    // Start a drag, then unmount before releasing the mouse.
    fireEvent.mouseDown(screen.getByTestId(PREVIEW_RESIZE_HANDLE), {
      clientX: GRAB_X,
    });
    unmount();

    // A held-button move after unmount must not reach a leaked listener (the
    // pre-#851 code added the window listeners imperatively inside the
    // mousedown callback, with no unmount cleanup).
    act(() => {
      window.dispatchEvent(
        new MouseEvent("mousemove", { clientX: GRAB_X - DRAG_DX, buttons: 1 })
      );
    });
    expect(useAppStore.getState().resultView.panelWidths.preview).toBe(START_WIDTH);
  });
});
