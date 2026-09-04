// useOverlayGeometry — the ONE drag/resize/persist mechanism for overlay
// windows (#739: FullResViewer, ExecuteDialog, ActionDialog).
//
// Owner sign-off (issue #739, 2026-07-17): "implement it once, apply to all
// three surfaces". This hook is that once. It carries no per-surface layout of
// its own — the first gesture seeds the geometry from the element's CURRENT
// rendered rect, so each surface keeps its existing default (the viewer fills
// the viewport; the two dialogs stay centered at their max-w-* size) until the
// user moves or resizes it.
//
// Gesture plumbing mirrors the #685 column-resize recipe (ColumnHeaderRow.tsx)
// and the #739 preview splitter (App.tsx): mousedown starts the drag, the
// window (not the 12px handle) is what tracks mousemove/mouseup so the gesture
// survives the cursor leaving the grab target, every move updates React state
// only, and localStorage is written ONCE on mouseup. Listeners are attached
// from a useEffect keyed on the active gesture — as #796 established, that is
// what guarantees React tears them down when the overlay unmounts mid-drag
// instead of leaking a window listener holding a stale closure.

import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent as ReactMouseEvent, RefObject } from "react";

import {
  clampGeometry,
  currentViewport,
  loadOverlayGeometry,
  saveOverlayGeometry,
  MIN_OVERLAY_HEIGHT,
  MIN_OVERLAY_WIDTH,
  type OverlayGeometry,
  type OverlayId,
  type Viewport,
} from "@/lib/overlayGeometry";

type GestureKind = "move" | "resize";

/**
 * Fraction of the viewport a maximized overlay un-maximizes to on drag.
 * Geometry is clamped fully inside the viewport, so this ratio also sets how
 * far the un-maximized window can then be dragged (10% of each axis on each
 * side). 0.8 keeps the viewer large while leaving a drag range a user can
 * actually see.
 */
const UNMAXIMIZE_RATIO = 0.8;

/** True when the rect covers the whole viewport (the full-res viewer's default). */
function fillsViewport(g: OverlayGeometry, viewport: Viewport): boolean {
  return g.w >= viewport.width - 1 && g.h >= viewport.height - 1;
}

/** A centered window at UNMAXIMIZE_RATIO of the viewport. */
function unmaximized(viewport: Viewport): OverlayGeometry {
  const w = Math.round(viewport.width * UNMAXIMIZE_RATIO);
  const h = Math.round(viewport.height * UNMAXIMIZE_RATIO);
  return {
    x: Math.round((viewport.width - w) / 2),
    y: Math.round((viewport.height - h) / 2),
    w,
    h,
  };
}

interface Gesture {
  kind: GestureKind;
  startX: number;
  startY: number;
  origin: OverlayGeometry;
}

export interface UseOverlayGeometry {
  /** Attach to the overlay's Radix Content element. */
  containerRef: RefObject<HTMLDivElement | null>;
  /**
   * Spread onto the Content's `style`. Empty while the overlay has never been
   * moved/resized in this browser, so the surface's own CSS layout applies.
   */
  style: CSSProperties;
  /** Title-bar `onMouseDown` — starts a move gesture. */
  onMoveStart: (e: ReactMouseEvent) => void;
  /** Corner-handle `onMouseDown` — starts a resize gesture. */
  onResizeStart: (e: ReactMouseEvent) => void;
  /** True while a move/resize is in flight (used to suppress text selection). */
  dragging: boolean;
}

/**
 * Movable + resizable + persisted geometry for one overlay surface.
 *
 * @param id      Which overlay (its own localStorage key).
 * @param open    Whether the overlay is currently mounted/open. Geometry is
 *                re-read from storage on each open so a value written by
 *                another tab, or a viewport that shrank while the overlay was
 *                closed, is re-clamped before the overlay is shown.
 */
export function useOverlayGeometry(
  id: OverlayId,
  open: boolean
): UseOverlayGeometry {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [geometry, setGeometry] = useState<OverlayGeometry | null>(null);
  const [gesture, setGesture] = useState<Gesture | null>(null);

  // Hydrate on every open (and re-clamp against the viewport as it is NOW).
  useEffect(() => {
    if (!open) return;
    setGeometry(loadOverlayGeometry(id));
  }, [id, open]);

  // Keep a moved/resized overlay reachable when the browser window shrinks:
  // re-clamp in place. A never-moved overlay (geometry === null) is left to
  // its own responsive CSS.
  useEffect(() => {
    if (!open) return;
    function onResize() {
      setGeometry((prev) =>
        prev === null ? prev : clampGeometry(prev, currentViewport())
      );
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open]);

  // Seed from the element's current rect so each surface keeps its own default.
  const readCurrentRect = useCallback((): OverlayGeometry | null => {
    const el = containerRef.current;
    if (el === null) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  }, []);

  const startGesture = useCallback(
    (kind: GestureKind, e: ReactMouseEvent) => {
      if (e.button !== 0) return;
      // A title bar can host controls (the viewer's close button sits in
      // its own). Pressing one must click it, not begin a window drag.
      if (
        kind === "move" &&
        e.target instanceof Element &&
        e.target.closest("button, a, input, select, textarea") !== null
      ) {
        return;
      }
      const seed = geometry ?? readCurrentRect();
      if (seed === null) return;
      const viewport = currentViewport();
      // Dragging a window that fills the whole viewport must un-maximize it
      // and follow the cursor — the OS window-manager convention. Without
      // this the full-res viewer, whose default layout IS the whole viewport,
      // would be clamped to x=0,y=0 and the title-bar drag would silently do
      // nothing, which is the exact complaint #739 was filed for. Inert for
      // the two dialogs: they never span the viewport.
      const origin =
        kind === "move" && fillsViewport(seed, viewport)
          ? unmaximized(viewport)
          : seed;
      e.preventDefault();
      // Stop a title-bar drag from also reaching whatever the title bar hosts
      // (the viewer's close button lives there, and Radix listens for
      // pointerdown on the content for its focus handling).
      e.stopPropagation();
      setGeometry(clampGeometry(origin, currentViewport()));
      setGesture({ kind, startX: e.clientX, startY: e.clientY, origin });
    },
    [geometry, readCurrentRect]
  );

  const onMoveStart = useCallback(
    (e: ReactMouseEvent) => startGesture("move", e),
    [startGesture]
  );
  const onResizeStart = useCallback(
    (e: ReactMouseEvent) => startGesture("resize", e),
    [startGesture]
  );

  useEffect(() => {
    if (gesture === null) return;
    const { kind, startX, startY, origin } = gesture;
    // The last geometry the move handler computed. mouseup persists THIS —
    // reading React state in the mouseup closure would persist a stale value.
    let latest = clampGeometry(origin, currentViewport());

    // ONE localStorage write per gesture — not one per mousemove. Shared by
    // EVERY end-of-gesture trigger below so all of them keep that contract.
    function endGesture() {
      saveOverlayGeometry(id, latest);
      setGesture(null);
    }

    function onMouseMove(ev: globalThis.MouseEvent) {
      // The button is already up: it was released somewhere we never heard
      // about — outside the browser window, with no focus change, so neither
      // `mouseup` nor `blur` reached us (#796/PR #840). Without this the next
      // move over the page would keep dragging the window with no button held.
      if (ev.buttons === 0) {
        endGesture();
        return;
      }
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      const next =
        kind === "move"
          ? { ...origin, x: origin.x + dx, y: origin.y + dy }
          : {
              ...origin,
              w: Math.max(MIN_OVERLAY_WIDTH, origin.w + dx),
              h: Math.max(MIN_OVERLAY_HEIGHT, origin.h + dy),
            };
      latest = clampGeometry(next, currentViewport());
      setGeometry(latest);
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", endGesture);
    // A release outside the window usually takes focus with it, and a
    // system-level gesture (touch/pen cancel, native drag) cancels the pointer
    // without a mouseup — end on both rather than leaving the window listeners
    // live and the overlay stuck to the cursor (#796/PR #840).
    window.addEventListener("blur", endGesture);
    window.addEventListener("pointercancel", endGesture);
    // Unconditional removal: the cleanup runs on every gesture-state change AND
    // on unmount, so no path can leave a window listener behind.
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", endGesture);
      window.removeEventListener("blur", endGesture);
      window.removeEventListener("pointercancel", endGesture);
    };
  }, [gesture, id]);

  const style: CSSProperties =
    geometry === null
      ? {}
      : {
          // Overrides the surfaces' layout classes: the viewer's `inset-0`
          // and the shared DialogContent's `left-1/2 top-1/2 -translate-*`
          // centering + `max-w-*`. right/bottom are neutralised explicitly so
          // `inset-0` cannot fight the explicit width/height.
          position: "fixed",
          left: geometry.x,
          top: geometry.y,
          right: "auto",
          bottom: "auto",
          width: geometry.w,
          height: geometry.h,
          maxWidth: "none",
          maxHeight: "none",
          // BOTH are required. Tailwind v4 compiles `-translate-x-1/2` to the
          // individual `translate` property (`translate: var(--tw-translate-x)
          // var(--tw-translate-y)`), which `transform` does NOT cancel — so
          // clearing only `transform` left the dialogs rendering half their
          // width to the LEFT of the stored x (measured live: geometry x=292
          // rendered at x=-156, i.e. partly off-screen, which defeats the
          // whole point of the viewport clamp). `transform` is still cleared
          // for any transform-based centering rule.
          translate: "none",
          transform: "none",
        };

  return {
    containerRef,
    style,
    onMoveStart,
    onResizeStart,
    dragging: gesture !== null,
  };
}
