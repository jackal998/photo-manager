// Cross-launch persistence for the resizable preview-panel width (#739).
//
// Mirrors the #685 column-width persistence recipe (columnWidths.ts) for a
// different boundary: the result-tree <-> preview-pane splitter. The Qt
// desktop persists the splitter ratio in window_state.ini (QSettings key
// geometry/main_splitter, features.md "Main window — geometry + splitter
// persistence"); the web analog is per-browser localStorage. Kept under its
// OWN key (`panelWidths`, distinct from the #685 column-widths key) since it
// is a separate persisted concept — the design doc (web-port-tech-design.md)
// and the s39 rework name this exact key.

/** Stable id for each resizable panel. Only one today (the preview pane);
 *  the map shape leaves room for a future panel without a key migration. */
export type PanelId = "preview";

const STORAGE_KEY = "panelWidths";

/** Default preview-pane width (px).
 *
 *  Layout slice PV (REPLY §"Preview pane", P1) moves this from 288 to 320:
 *  «320 rather than the prototype's 340: at 1280 with the table's column
 *  budget, 340 is where the filename column starts losing characters, and 20px
 *  of pane is cheaper than 20px of name». The pane now carries a title strip, a
 *  4:3 image frame, a five-row metadata table and a stacked decision block, and
 *  288 is where the zh-TW decision labels start wrapping. */
export const DEFAULT_PANEL_WIDTHS: Record<PanelId, number> = {
  preview: 320,
};

/** The defaults this key was written with BEFORE slice PV.
 *
 *  P1 is a MAY-DIFFER row: «the number is soft and a persisted user value
 *  wins». But a value that equals the OLD DEFAULT is not a user value — it is
 *  what an unresized pane persisted (the drag handler writes the whole map on
 *  any resize, so every browser that ever touched the splitter has a `preview`
 *  entry, most of them still 288). Migrating exactly that one value is what
 *  makes the new default reach users who never dragged; anything else the user
 *  chose is left alone. */
export const LEGACY_DEFAULT_PANEL_WIDTHS: Record<PanelId, number> = {
  preview: 288,
};

/** Minimum width the preview pane can be resized to (px) — mirrors the
 *  desktop splitter's 200px floor (features.md #136). */
export const MIN_PANEL_WIDTH = 200;

/** Maximum width as a fraction of the viewport — keeps the result tree from
 *  being squeezed to nothing on an overshooting drag. */
export const MAX_PANEL_WIDTH_RATIO = 0.6;

const PANEL_IDS = Object.keys(DEFAULT_PANEL_WIDTHS) as PanelId[];

/**
 * Clamp a candidate width to [MIN_PANEL_WIDTH, 60% of the current viewport].
 * The upper bound reads the LIVE `window.innerWidth` so it tracks window
 * resizes; falls back to no upper bound where `window` is unavailable.
 */
export function clampPanelWidth(width: number): number {
  const viewportMax =
    typeof window !== "undefined"
      ? Math.round(window.innerWidth * MAX_PANEL_WIDTH_RATIO)
      : Number.POSITIVE_INFINITY;
  // Floor-guard the ceiling: on a very narrow viewport (where 60% of the width
  // is below the 200px floor) the min-clamp must still win, so the preview
  // pane can never be forced below MIN_PANEL_WIDTH.
  const maxWidth = Math.max(MIN_PANEL_WIDTH, viewportMax);
  return Math.min(Math.max(MIN_PANEL_WIDTH, Math.round(width)), maxWidth);
}

/**
 * Read persisted panel widths from localStorage, merged over the defaults.
 * Fail-open: any parse/storage error (private-mode, malformed blob, missing
 * key) falls back to the defaults so the layout always renders.
 */
export function loadPanelWidths(): Record<PanelId, number> {
  const widths: Record<PanelId, number> = { ...DEFAULT_PANEL_WIDTHS };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return widths;
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object") {
      for (const id of PANEL_IDS) {
        const v = (parsed as Record<string, unknown>)[id];
        // Only accept finite positive numbers; ignore anything else so a
        // corrupt entry can't render a panel at 0 / NaN width.
        if (typeof v === "number" && Number.isFinite(v) && v > 0) {
          // Slice PV's one-way migration: a stored value that is EXACTLY the
          // old default is an unresized pane, so it follows the new default;
          // any other number is the user's own drag and survives untouched.
          widths[id] = v === LEGACY_DEFAULT_PANEL_WIDTHS[id]
            ? DEFAULT_PANEL_WIDTHS[id]
            : v;
        }
      }
    }
  } catch {
    // fail-open — defaults already populated
  }
  return widths;
}

/**
 * Persist the full panel-width map to localStorage. Fail-open: a storage
 * error (quota, private mode) is swallowed — width persistence is a
 * convenience, never a correctness invariant.
 */
export function savePanelWidths(widths: Record<PanelId, number>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
  } catch {
    // fail-open — in-session width still applies for this page life
  }
}
