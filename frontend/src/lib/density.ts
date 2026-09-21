// Result-view DENSITY preference (#878, layout slice R).
//
// The layout REPLY (2026-09-18, L3 "Row heights") specifies every row metric
// twice — once comfortable, once compact — so the preference is a first-class
// piece of view state rather than a CSS media query: it is the USER's call, it
// has to survive a reload, and the virtualiser needs to read the same number
// the row renders at.
//
// Persistence mirrors lib/panelWidths.ts (#739) rather than inventing a second
// recipe: its own localStorage key, hydrated once at store creation, merged
// over the default, fail-open on any storage error.
//
// This slice owns the preference and its EFFECT. The toggle UI that lets a
// user change it lives in the status bar and belongs to layout slice TB.

/** The two value sets the REPLY specifies. Default is `comfortable`. */
export type Density = "comfortable" | "compact";

const STORAGE_KEY = "density";

export const DEFAULT_DENSITY: Density = "comfortable";

function isDensity(value: unknown): value is Density {
  return value === "comfortable" || value === "compact";
}

/**
 * Read the persisted density from localStorage.
 *
 * Fail-open: a missing key, a corrupt value, or a storage error (private mode,
 * disabled cookies) all fall back to `comfortable` — a density preference is a
 * convenience, never a correctness invariant, and a row list that refuses to
 * render is strictly worse than one at the wrong height.
 */
export function loadDensity(): Density {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isDensity(raw) ? raw : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

/** Persist the density. Fail-open, same reasoning as `loadDensity`. */
export function saveDensity(density: Density): void {
  try {
    localStorage.setItem(STORAGE_KEY, density);
  } catch {
    // fail-open — the in-session preference still applies for this page life
  }
}
