// Unit cover for the shared overlay drag/resize/persist mechanism (#739).
//
// The bugs these tests exist to catch are the ones the three surfaces would
// hit in a real session:
//   - a drag that writes localStorage on every mousemove (hundreds of writes
//     per gesture — the exact thing the #685/#739 recipe commits ONCE for);
//   - a resize that lets the window shrink below a usable size;
//   - the gesture continuing to move the window after the mouse is released,
//     or window listeners surviving an unmount mid-drag holding a stale
//     closure (the #796 leak class);
//   - a mousedown on a control inside the title bar starting a window drag
//     instead of clicking the control (the full-res viewer's close button).

import { render, screen, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useOverlayGeometry } from "./useOverlayGeometry";
import {
  loadOverlayGeometry,
  MIN_OVERLAY_HEIGHT,
  MIN_OVERLAY_WIDTH,
} from "@/lib/overlayGeometry";

const VIEWPORT = { width: 1280, height: 800 };
const RECT = { left: 300, top: 200, width: 640, height: 480 };
/** Every window event the gesture registers — and must unregister. */
const DRAG_EVENT_TYPES = ["mousemove", "mouseup", "blur", "pointercancel"];

/** A minimal stand-in for a Radix Content: a box with a title bar and a grip. */
function Harness({ open = true }: { open?: boolean }) {
  const overlay = useOverlayGeometry("execute", open);
  if (!open) return null;
  return (
    <div ref={overlay.containerRef} data-testid="box" style={overlay.style}>
      <div data-testid="titlebar" onMouseDown={overlay.onMoveStart}>
        <button data-testid="close">x</button>
      </div>
      <div data-testid="grip" onMouseDown={overlay.onResizeStart} />
    </div>
  );
}

function mouseDown(testid: string, x: number, y: number): void {
  const el = screen.getByTestId(testid);
  act(() => {
    el.dispatchEvent(
      new MouseEvent("mousedown", {
        bubbles: true,
        button: 0,
        clientX: x,
        clientY: y,
      })
    );
  });
}

/** A move DURING a drag: a real one always reports the held button. A move
 *  with buttons=0 means the button was released unseen — see the #796 tests. */
function mouseMove(x: number, y: number, buttons = 1): void {
  act(() => {
    window.dispatchEvent(
      new MouseEvent("mousemove", {
        bubbles: true,
        clientX: x,
        clientY: y,
        buttons,
      })
    );
  });
}

function mouseUp(): void {
  act(() => {
    window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
}

function boxStyle(): CSSStyleDeclaration {
  return screen.getByTestId("box").style;
}

beforeEach(() => {
  localStorage.clear();
  window.innerWidth = VIEWPORT.width;
  window.innerHeight = VIEWPORT.height;
  // jsdom reports a zero rect for every element, so the seed-from-rendered-rect
  // path needs a stub. This is the surfaces' real default-layout source: the
  // hook carries no per-surface default of its own.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    ...RECT,
    right: RECT.left + RECT.width,
    bottom: RECT.top + RECT.height,
    x: RECT.left,
    y: RECT.top,
    toJSON: () => ({}),
  } as DOMRect);
});

describe("moving", () => {
  it("has NO inline geometry before the first gesture, so the surface's own layout applies", () => {
    render(<Harness />);
    expect(boxStyle().left).toBe("");
    expect(boxStyle().width).toBe("");
  });

  it("moves the window by the drag delta, seeded from its rendered rect", () => {
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    expect(boxStyle().left).toBe("400px"); // 300 + 100
    expect(boxStyle().top).toBe("280px"); // 200 + 80
    // Size is untouched by a move.
    expect(boxStyle().width).toBe("640px");
    expect(boxStyle().height).toBe("480px");
  });

  it("cancels BOTH the transform- and translate-based centering", () => {
    // The two dialogs are centered with Tailwind's `-translate-x-1/2
    // -translate-y-1/2`, which Tailwind v4 compiles to the individual
    // `translate` property — `transform: none` does not cancel it. Leaving it
    // in place rendered a moved dialog half its width to the LEFT of its
    // stored x (live-measured: stored 292, rendered -156), i.e. partly
    // off-screen, which silently defeats the viewport clamp.
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    expect(boxStyle().translate).toBe("none");
    expect(boxStyle().transform).toBe("none");
  });

  it("stops following the pointer after mouseup", () => {
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    mouseUp();
    mouseMove(900, 700);
    expect(boxStyle().left).toBe("400px");
  });

  it("cannot be dragged off-screen", () => {
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(-5000, -5000);
    expect(boxStyle().left).toBe("0px");
    expect(boxStyle().top).toBe("0px");
    mouseMove(9000, 9000);
    // 1280 - 640 = 640 ; 800 - 480 = 320
    expect(boxStyle().left).toBe("640px");
    expect(boxStyle().top).toBe("320px");
  });

  it("un-maximizes a viewport-filling window on drag so the gesture is not a no-op", () => {
    // The full-res viewer's default layout IS the whole viewport. Clamped as
    // a full-size window it could only ever sit at 0,0 — the title-bar drag
    // would appear dead, which is what #739 was filed for. Dragging must
    // shrink it and follow the cursor (the OS window-manager convention).
    (Element.prototype.getBoundingClientRect as unknown as ReturnType<typeof vi.fn>)
      .mockReturnValue({
        left: 0,
        top: 0,
        width: VIEWPORT.width,
        height: VIEWPORT.height,
        right: VIEWPORT.width,
        bottom: VIEWPORT.height,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect);
    render(<Harness />);
    mouseDown("titlebar", 600, 20);
    mouseMove(700, 60);
    const w = Number.parseInt(boxStyle().width, 10);
    const h = Number.parseInt(boxStyle().height, 10);
    expect(w).toBe(Math.round(VIEWPORT.width * 0.8));
    expect(h).toBe(Math.round(VIEWPORT.height * 0.8));
    // 80%-centered origin (128, 80) plus the (+100, +40) drag delta — both
    // still inside the clamp range (0..256 x, 0..160 y).
    expect(boxStyle().left).toBe("228px");
    expect(boxStyle().top).toBe("120px");
  });

  it("does not start a drag from a control inside the title bar", () => {
    render(<Harness />);
    mouseDown("close", 400, 250);
    mouseMove(600, 400);
    // No geometry was applied at all — the close button click is intact.
    expect(boxStyle().left).toBe("");
  });
});

describe("resizing", () => {
  it("grows the window by the drag delta without moving its origin", () => {
    render(<Harness />);
    mouseDown("grip", 940, 680);
    mouseMove(1040, 760);
    expect(boxStyle().width).toBe("740px"); // 640 + 100
    expect(boxStyle().height).toBe("560px"); // 480 + 80
    expect(boxStyle().left).toBe("300px");
    expect(boxStyle().top).toBe("200px");
  });

  it("refuses to shrink below the minimum usable size", () => {
    render(<Harness />);
    mouseDown("grip", 940, 680);
    mouseMove(0, 0);
    expect(boxStyle().width).toBe(`${MIN_OVERLAY_WIDTH}px`);
    expect(boxStyle().height).toBe(`${MIN_OVERLAY_HEIGHT}px`);
  });
});

describe("persistence", () => {
  it("writes localStorage once per drag gesture, not per mousemove", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    for (let i = 1; i <= 20; i += 1) mouseMove(400 + i, 250 + i);
    expect(setItem).not.toHaveBeenCalled();
    mouseUp();
    expect(setItem).toHaveBeenCalledTimes(1);
    setItem.mockRestore();
  });

  it("persists the FINAL position of the gesture, not the first move", () => {
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(410, 260);
    mouseMove(500, 330);
    mouseUp();
    expect(loadOverlayGeometry("execute", VIEWPORT)).toEqual({
      x: 400,
      y: 280,
      w: 640,
      h: 480,
    });
  });

  it("restores the saved geometry when the overlay is reopened", () => {
    const { rerender } = render(<Harness open={true} />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    mouseUp();
    rerender(<Harness open={false} />);
    rerender(<Harness open={true} />);
    expect(boxStyle().left).toBe("400px");
    expect(boxStyle().top).toBe("280px");
  });

  it("re-clamps in place when the browser window shrinks while the overlay is OPEN", () => {
    // Shrinking the browser under an open dialog would otherwise leave it
    // hanging off the new viewport, with its footer buttons unreachable and
    // no way to drag it back.
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(900, 600);
    mouseUp();
    act(() => {
      window.innerWidth = 700;
      window.innerHeight = 500;
      window.dispatchEvent(new Event("resize"));
    });
    const left = Number.parseInt(boxStyle().left, 10);
    const top = Number.parseInt(boxStyle().top, 10);
    const w = Number.parseInt(boxStyle().width, 10);
    const h = Number.parseInt(boxStyle().height, 10);
    expect(left + w).toBeLessThanOrEqual(700);
    expect(top + h).toBeLessThanOrEqual(500);
  });

  it("re-clamps a stored geometry against a viewport that shrank while closed", () => {
    const { rerender } = render(<Harness open={true} />);
    mouseDown("titlebar", 400, 250);
    mouseMove(900, 700); // near the bottom-right of the 1280x800 viewport
    mouseUp();
    rerender(<Harness open={false} />);
    window.innerWidth = 800;
    window.innerHeight = 600;
    rerender(<Harness open={true} />);
    const left = Number.parseInt(boxStyle().left, 10);
    const top = Number.parseInt(boxStyle().top, 10);
    const w = Number.parseInt(boxStyle().width, 10);
    const h = Number.parseInt(boxStyle().height, 10);
    expect(left + w).toBeLessThanOrEqual(800);
    expect(top + h).toBeLessThanOrEqual(600);
  });
});

// The button can be released where the page never hears about it: over another
// application (no mouseup), or via a system gesture that cancels the pointer.
// Without an end-of-gesture trigger for each, the overlay stays glued to the
// cursor on the next move — the #796 bug, on this mechanism (PR #840).
describe("ending a gesture the window never saw released (#796)", () => {
  it("ends the drag on window blur (button released over another app)", () => {
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    act(() => {
      window.dispatchEvent(new Event("blur"));
    });
    // Deliberately buttons=1 on the follow-up move: a buttons=0 move would be
    // caught by the separate no-button guard below, and this test would then
    // pass with the blur listener deleted (verified by mutation).
    mouseMove(900, 700, 1);
    expect(boxStyle().left).toBe("400px");
    expect(boxStyle().top).toBe("280px");
  });

  it("ends the drag on pointercancel", () => {
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    act(() => {
      window.dispatchEvent(new Event("pointercancel"));
    });
    mouseMove(900, 700, 1); // buttons=1 for the same isolation reason
    expect(boxStyle().left).toBe("400px");
  });

  it("ends the drag on the first move that arrives with no button held", () => {
    // The release happened outside the window and took no focus, so neither
    // mouseup nor blur ever arrives — this move is the only signal.
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    mouseMove(900, 700, 0);
    expect(boxStyle().left).toBe("400px");
    // And it stays ended for every later move.
    mouseMove(1100, 700, 0);
    expect(boxStyle().left).toBe("400px");
  });

  it("still persists exactly once when the gesture ends via blur", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    render(<Harness />);
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    act(() => {
      window.dispatchEvent(new Event("blur"));
    });
    expect(setItem).toHaveBeenCalledTimes(1);
    expect(loadOverlayGeometry("execute", VIEWPORT)).toMatchObject({ x: 400 });
    setItem.mockRestore();
  });
});

describe("listener lifecycle", () => {
  it("removes every window listener it added when the overlay unmounts mid-drag", () => {
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    const { unmount } = render(<Harness />);
    add.mockClear();
    remove.mockClear();
    mouseDown("titlebar", 400, 250);
    mouseMove(500, 330);
    const added = add.mock.calls
      .map((c) => c[0])
      .filter((t) => DRAG_EVENT_TYPES.includes(t as string));
    // Vacuity guard: if the gesture registered nothing, "all removed" is
    // trivially true and this test would pass against a broken mechanism.
    expect(added.sort()).toEqual([...DRAG_EVENT_TYPES].sort());
    unmount();
    const removed = remove.mock.calls
      .map((c) => c[0])
      .filter((t) => DRAG_EVENT_TYPES.includes(t as string));
    for (const type of added) expect(removed).toContain(type);
    add.mockRestore();
    remove.mockRestore();
  });
});
