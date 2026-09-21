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

/**
 * Viewport-relative overlay geometry, in CSS pixels.
 *
 * `w`/`h` are **null until the user actually resizes**. That asymmetry is the
 * whole point: moving a window must not freeze its size. A dialog whose height
 * was pinned by a mere drag cannot grow when new content appears (the Set
 * Action preview block), so the content spills past the footer and the Apply
 * button ends up outside the box — measured live before this rule existed:
 * dialog bottom 606, Apply bottom 716. Null size means "let the surface's own
 * CSS decide", which is also what keeps an unresized overlay identical to base.
 */
export interface OverlayGeometry {
  x: number;
  y: number;
  w: number | null;
  h: number | null;
}

/** A concrete rendered size, used as the fallback when `w`/`h` are null. */
export interface OverlaySize {
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
/**
 * Chosen from a live measurement, not by feel: with the Execute dialog's
 * fixed chrome (header + toolbar + all-delete banner + footer ≈ 200px) a
 * 260px box left the file tree 60px — exactly one row, and exactly on the
 * scenario's failure threshold. 280 leaves ~80px of body, so the floor keeps
 * a usable body rather than only just fitting the chrome.
 */
export const MIN_OVERLAY_HEIGHT = 280;

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
  viewport: Viewport,
  measured: OverlaySize
): OverlayGeometry {
  // A pinned size is clamped to [MIN, viewport]; an unpinned one stays null and
  // the element's CURRENT rendered size stands in for the position maths, so a
  // restore can place an auto-sized dialog correctly without ever pinning it.
  const w =
    geometry.w === null
      ? null
      : Math.min(
          Math.max(MIN_OVERLAY_WIDTH, Math.round(geometry.w)),
          Math.max(MIN_OVERLAY_WIDTH, Math.round(viewport.width))
        );
  const h =
    geometry.h === null
      ? null
      : Math.min(
          Math.max(MIN_OVERLAY_HEIGHT, Math.round(geometry.h)),
          Math.max(MIN_OVERLAY_HEIGHT, Math.round(viewport.height))
        );
  const effectiveW = w ?? Math.round(measured.w);
  const effectiveH = h ?? Math.round(measured.h);
  const x = Math.min(
    Math.max(0, Math.round(geometry.x)),
    Math.max(0, Math.round(viewport.width) - effectiveW)
  );
  const y = Math.min(
    Math.max(0, Math.round(geometry.y)),
    Math.max(0, Math.round(viewport.height) - effectiveH)
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
export function loadOverlayGeometry(id: OverlayId): OverlayGeometry | null {
  try {
    const raw = localStorage.getItem(overlayStorageKey(id));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const { x, y, w, h } = parsed as Record<string, unknown>;
    // Position is mandatory: a blob without a usable x/y is discarded whole
    // rather than merged over a default, which would place the overlay
    // somewhere nobody chose.
    if (!isFiniteNumber(x) || !isFiniteNumber(y)) return null;
    // Size is OPTIONAL — absent means "never resized, use the surface's own
    // layout". A present-but-nonsense size degrades to null (auto) rather than
    // discarding the position the user did choose.
    const usableW = isFiniteNumber(w) && w > 0 ? w : null;
    const usableH = isFiniteNumber(h) && h > 0 ? h : null;
    // NOT clamped here: clamping the position needs the element's rendered
    // size, which only exists once it is on screen. useOverlayGeometry clamps
    // in a layout effect, against the real box.
    return { x, y, w: usableW, h: usableH };
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
