// Cross-launch persistence for movable/resizable overlay windows (#739).
//
// The web analog of Qt's per-dialog QSettings geometry round-trip
// (features.md "Main window — geometry + splitter persistence" and the
// QDialog.done() save hooks behind qa/scenarios/s48_dialog_geometry_persist.py).
// Qt stores a saveGeometry() blob per dialog under geometry/<dialog>; the web
// stores {x, y, w, h} per overlay in localStorage.
//
// Storage shape: ONE key PER surface (`pm.overlay-geometry.<id>.v1`), so a
// corrupt or stale blob for one overlay can never take the other two down with
// it, and so a future surface needs no key migration. Kept deliberately
// separate from the `panelWidths` key (#739 preview splitter) and the
// `pm.result-tree.column-widths.v1` key (#685) — three distinct persisted
// concepts. Fail-open throughout, mirroring panelWidths.ts: geometry is a
// convenience, never a correctness invariant, so any storage/parse error
// falls back to the surface's own default layout.

/** Stable id for each overlay whose geometry persists. */
export type OverlayId = "fullres" | "execute" | "action";

/** Viewport-relative overlay rectangle, in CSS pixels. */
export interface OverlayGeometry {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Viewport box the geometry is clamped against. */
export interface Viewport {
  width: number;
  height: number;
}

/**
 * Minimum overlay size (px). Below this a window has no usable title bar or
 * body — the web analog of Qt's dialog minimumSize. A saved geometry smaller
 * than this (hand-edited storage, or a resize that ran past the floor) is
 * clamped back up so an overlay can never be shrunk to unreachable.
 */
export const MIN_OVERLAY_WIDTH = 320;
export const MIN_OVERLAY_HEIGHT = 200;

/** localStorage key for one overlay. Per-surface, versioned. */
export function overlayStorageKey(id: OverlayId): string {
  return `pm.overlay-geometry.${id}.v1`;
}

/**
 * Clamp a geometry into `viewport`.
 *
 * Two failure modes this exists to prevent, both reachable with a perfectly
 * ordinary saved geometry:
 *   - the user saved the overlay near the right/bottom edge of a large monitor
 *     and reopened the app in a smaller window, so the title bar (the only
 *     drag affordance) is off-screen and the overlay can never be moved back;
 *   - the saved size is larger than the current viewport, so the buttons along
 *     its bottom edge are unreachable.
 *
 * Size is clamped first (floor = MIN_*, ceiling = the viewport), then the
 * origin, so the result is always fully inside the viewport. On a viewport
 * narrower than the minimum size the min-clamp wins and x/y pin to 0 —
 * an overflowing overlay beats a zero-sized one.
 */
export function clampGeometry(
  geometry: OverlayGeometry,
  viewport: Viewport
): OverlayGeometry {
  const w = Math.min(
    Math.max(MIN_OVERLAY_WIDTH, Math.round(geometry.w)),
    Math.max(MIN_OVERLAY_WIDTH, Math.round(viewport.width))
  );
  const h = Math.min(
    Math.max(MIN_OVERLAY_HEIGHT, Math.round(geometry.h)),
    Math.max(MIN_OVERLAY_HEIGHT, Math.round(viewport.height))
  );
  const x = Math.min(
    Math.max(0, Math.round(geometry.x)),
    Math.max(0, Math.round(viewport.width) - w)
  );
  const y = Math.min(
    Math.max(0, Math.round(geometry.y)),
    Math.max(0, Math.round(viewport.height) - h)
  );
  return { x, y, w, h };
}

/** Read the live viewport, falling back to a sane box where `window` is absent. */
export function currentViewport(): Viewport {
  if (typeof window === "undefined") {
    return { width: MIN_OVERLAY_WIDTH, height: MIN_OVERLAY_HEIGHT };
  }
  return { width: window.innerWidth, height: window.innerHeight };
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/**
 * Read one overlay's persisted geometry, clamped to the CURRENT viewport.
 *
 * Returns `null` when nothing valid is stored, which every caller reads as
 * "use this surface's own default layout" — that is what keeps the first open
 * of each overlay pixel-identical to before this change.
 */
export function loadOverlayGeometry(
  id: OverlayId,
  viewport: Viewport = currentViewport()
): OverlayGeometry | null {
  try {
    const raw = localStorage.getItem(overlayStorageKey(id));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const { x, y, w, h } = parsed as Record<string, unknown>;
    // All four must be finite numbers, and the size positive — a partial or
    // corrupt blob is discarded whole rather than merged over a default,
    // which would place the overlay somewhere nobody chose.
    if (!isFiniteNumber(x) || !isFiniteNumber(y)) return null;
    if (!isFiniteNumber(w) || !isFiniteNumber(h)) return null;
    if (w <= 0 || h <= 0) return null;
    return clampGeometry({ x, y, w, h }, viewport);
  } catch {
    // fail-open — the surface renders at its default layout
    return null;
  }
}

/**
 * Persist one overlay's geometry. Called ONCE at the end of a drag/resize
 * gesture (not per mousemove) — same contract as the #685 column resize and
 * the #739 preview splitter. Fail-open: a quota/private-mode error leaves the
 * in-session geometry applied for this page life.
 */
export function saveOverlayGeometry(
  id: OverlayId,
  geometry: OverlayGeometry
): void {
  try {
    localStorage.setItem(overlayStorageKey(id), JSON.stringify(geometry));
  } catch {
    // fail-open
  }
}
