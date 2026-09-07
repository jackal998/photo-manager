// Cross-launch persistence for result-tree column widths (#685 → s47).
//
// The Qt desktop persists column widths in window_state.ini — a per-MACHINE
// UI-chrome file kept SEPARATE from settings.json (which holds app config like
// sources/thresholds). The web port already maps settings.json → /api/settings;
// the faithful analog of the per-machine UI-chrome file is the per-browser
// localStorage. This also keeps widths a pure client concern (no backend
// change, the issue's explicit constraint) and gives free test isolation —
// localStorage is auto-scoped per Playwright browser context, so s47 needs no
// server-side reset (unlike the shared settings.json that s23a/s23b reset).

import { DEFAULT_COLUMN_WIDTHS, type ColumnId } from "./resultColumns";

// Versioned key so a future column-schema change can invalidate stale widths
// rather than restore a layout for columns that no longer exist.
const STORAGE_KEY = "pm.result-tree.column-widths.v1";

const COLUMN_IDS = Object.keys(DEFAULT_COLUMN_WIDTHS) as ColumnId[];

/**
 * Read persisted column widths from localStorage, merged over the defaults.
 * Fail-open: any parse/storage error (private-mode, malformed blob, missing
 * key) falls back to the defaults so the tree always renders.
 */
export function loadColumnWidths(): Record<ColumnId, number> {
  const widths: Record<ColumnId, number> = { ...DEFAULT_COLUMN_WIDTHS };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return widths;
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object") {
      for (const id of COLUMN_IDS) {
        const v = (parsed as Record<string, unknown>)[id];
        // Only accept finite positive numbers; ignore anything else so a
        // corrupt entry can't render a column at 0 / NaN width.
        if (typeof v === "number" && Number.isFinite(v) && v > 0) {
          widths[id] = v;
        }
      }
    }
  } catch {
    // fail-open — defaults already populated
  }
  return widths;
}

/**
 * Persist the full column-width map to localStorage. Fail-open: a storage
 * error (quota, private mode) is swallowed — width persistence is a
 * convenience, never a correctness invariant.
 */
export function saveColumnWidths(widths: Record<ColumnId, number>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
  } catch {
    // fail-open — in-session widths still apply for this page life
  }
}
