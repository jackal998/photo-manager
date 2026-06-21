// Pure formatter functions — no React, no side effects.
// Used by FileRow cells and summary displays.

import type { Similarity } from "../api/types";

/** Em dash used for null / unavailable values. */
const EM_DASH = "—";

/** Compile-time exhaustiveness guard. Throws at runtime if somehow reached. */
function assertNever(x: never): string {
  throw new Error(`Unhandled similarity kind: ${String(x)}`);
}

/**
 * Return a short human-readable label for a Similarity value.
 *
 * Rendering rules (English; i18n deferred to Phase 2D):
 *   kind "percent"   → "92%"
 *   kind "ref"       → "Ref"
 *   kind "passenger" → "★ 92%"
 *   kind "near_dup"  → "~"
 *   kind "none"      → "—"
 */
export function similarityLabel(similarity: Similarity): string {
  switch (similarity.kind) {
    case "percent":
      return similarity.percent !== null
        ? `${Math.round(similarity.percent)}%`
        : EM_DASH;
    case "ref":
      return "Ref";
    case "passenger":
      return similarity.percent !== null
        ? `★ ${Math.round(similarity.percent)}%`
        : "★";
    case "near_dup":
      return "~";
    case "none":
      return EM_DASH;
    default:
      // Exhaustiveness guard: assertNever pattern — TypeScript errors here if
      // a new SimilarityKind is added without a matching case above.
      return assertNever(similarity.kind);
  }
}

/**
 * Format a byte count as a human-readable string (KB / MB / GB).
 *
 * Uses 1024-based units. Values below 1 KB are shown as "N B".
 */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/**
 * Format a score as a one-decimal string, or an em dash if null.
 */
export function formatScore(score: number | null): string {
  if (score === null) return EM_DASH;
  return score.toFixed(1);
}

/**
 * Format an ISO date string as a locale date, or an em dash if null.
 *
 * Uses the browser's default locale. Only the date portion is shown
 * (no time) because shot_date / creation_date precision is day-level
 * in the manifest.
 */
export function formatDate(iso: string | null): string {
  if (iso === null) return EM_DASH;
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return EM_DASH;
  }
}

/**
 * Format pixel dimensions as "W × H", or an em dash if either is null.
 */
export function formatDims(w: number | null, h: number | null): string {
  if (w === null || h === null) return EM_DASH;
  return `${w} × ${h}`;
}
