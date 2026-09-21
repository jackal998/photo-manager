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
 * Format a score as a TWO-decimal string, or an em dash if null.
 *
 * Layout REPLY L2 (2026-09-18), cell table: «score number · 11px mono · 600 ·
 * right · two decimals, always». One decimal collapsed most of a duplicate
 * group to the same number — scores in a near-duplicate group differ in the
 * second place far more often than in the first, and the whole point of the
 * cell is to say why one row was chosen over its neighbour.
 */
export function formatScore(score: number | null): string {
  if (score === null) return EM_DASH;
  return score.toFixed(2);
}

/**
 * Format an ISO date string as a locale date, or an em dash if null.
 *
 * Layout REPLY L2: «`14 Mar 2021` — no time, no seconds». The month is spelled
 * rather than numbered because `3/14/2021` and `14/3/2021` are the same six
 * characters in two incompatible orders, and a column of dates is read by
 * scanning, not by parsing.
 *
 * Still LOCALE-AWARE: the field order (and the month's spelling) comes from a
 * locale, not from a hardcoded template. Pass the APP's locale via
 * `useDateLocale()` — omitting it reads the BROWSER's, which is how the
 * English UI came to print Chinese dates. Only the date portion is shown,
 * because shot_date / creation_date precision is day-level in the manifest.
 */
/**
 * The BCP-47 tag to format dates with, for one of the app's UI locales.
 *
 * `formatDate` takes an optional locale and, before this, every caller passed
 * `undefined` — which means "the BROWSER's locale". On a Taiwanese machine the
 * English UI therefore printed 「2024年2月1日」 in the Shot Date column, i.e.
 * the app's own language switch did not reach its dates. `en-GB` rather than
 * `en-US` because the REPLY's L2 cell table spells the format `14 Mar 2021`,
 * day before month.
 *
 * Unknown codes fall back to `en-GB` rather than to the browser: the point is
 * that the UI decides, and an unrecognised app locale is a bug in this map,
 * not an invitation to read the OS again.
 */
const _DATE_LOCALES: Record<string, string> = {
  en: "en-GB",
  zh_TW: "zh-TW",
};

export function dateLocaleFor(appLocale: string): string {
  return _DATE_LOCALES[appLocale] ?? "en-GB";
}

export function formatDate(iso: string | null, locale?: string): string {
  if (iso === null) return EM_DASH;
  try {
    return new Date(iso).toLocaleDateString(locale, {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return EM_DASH;
  }
}

/**
 * Format pixel dimensions as "W×H", or an em dash if either is null.
 *
 * Layout REPLY L2: «`4000×3000`, no spaces around ×». The cell is right-
 * aligned mono in a 88px column; the two spaces bought nothing and cost the
 * column its whole margin at four-digit resolutions.
 */
export function formatDims(w: number | null, h: number | null): string {
  if (w === null || h === null) return EM_DASH;
  return `${w}×${h}`;
}
