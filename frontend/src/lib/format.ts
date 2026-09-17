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
 * Rendering rules:
 *   kind "percent"   → "92%"
 *   kind "ref"       → "Ref" (translatable)
 *   kind "passenger" → "★ 92%"
 *   kind "near_dup"  → "~dup" (translatable; was a bare hardcoded "~")
 *   kind "none"      → "—"
 *
 * Stays a pure function (no store import, no hooks — this module is used
 * outside React too) by accepting an optional translate function; callers
 * pass useT()'s `t`. Omitting it (existing tests, non-React callers) falls
 * back to the English default, same as before.
 */
export function similarityLabel(
  similarity: Similarity,
  t: (key: string, fallback: string) => string = (_key, fallback) => fallback
): string {
  switch (similarity.kind) {
    case "percent":
      return similarity.percent !== null
        ? `${Math.round(similarity.percent)}%`
        : EM_DASH;
    case "ref":
      return t("web.format.similarity_ref", "Ref");
    case "passenger":
      return similarity.percent !== null
        ? `★ ${Math.round(similarity.percent)}%`
        : "★";
    case "near_dup":
      // Copy audit R6: a bare "~" carries no meaning and no translation,
      // while the desktop (tree.similarity_near_dup) and docs/features.md
      // both document this cell as "~dup".
      return t("web.format.similarity_near_dup", "~dup");
    case "none":
      return EM_DASH;
    default:
      // Exhaustiveness guard: assertNever pattern — TypeScript errors here if
      // a new SimilarityKind is added without a matching case above.
      return assertNever(similarity.kind);
  }
}

/**
 * The scanner's classification values (`scanner/dedup.py`) as they arrive on
 * `FileRow.action`, mapped to their catalog key. `""` has no classification.
 *
 * Copy audit R1 (values half): the Action column rendered this enum verbatim,
 * so a zh_TW user read "REVIEW_DUPLICATE" exactly like an en user did.
 * INTERIM — whether this column keeps carrying the classification at all is a
 * separate layout decision; this only stops the raw enum reaching the user.
 */
const CLASSIFICATION_KEYS: Record<string, string> = {
  EXACT: "exact",
  REVIEW_DUPLICATE: "review_duplicate",
  KEEP: "keep",
  UNDATED: "undated",
};

/** English defaults, used when no catalog is loaded (same shape as above). */
const CLASSIFICATION_DEFAULTS: Record<string, string> = {
  exact: "Exact copy",
  review_duplicate: "Near-duplicate",
  keep: "Best in group",
  undated: "No date",
};

/**
 * Human-readable label for a classification value, or the em dash when the
 * row carries none. An UNRECOGNISED value is returned verbatim rather than
 * swallowed — a new classifier output must be visible, not silently blank.
 */
export function classificationLabel(
  action: string,
  t: (key: string, fallback: string) => string = (_key, fallback) => fallback
): string {
  if (!action) return EM_DASH;
  const key = CLASSIFICATION_KEYS[action];
  if (key === undefined) return action;
  return t(`web.classification.${key}`, CLASSIFICATION_DEFAULTS[key]);
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
