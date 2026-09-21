// recentPatterns.ts — pure read/write helpers for the ActionDialog "Recent"
// affordance (#741 sub-item B).
//
// Qt analog: app/views/dialogs/select_dialog.py — the ``_recent_patterns``
// list (a list of (field, pattern) tuples), ``_record_recent_pattern``
// (dedup + cap), and ``_show_recent_menu`` (per-field visibility filter).
//
// Persisted under the settings key ``ui.action_dialog.recent_patterns`` —
// the SAME key Qt uses, so entries are shared across the desktop and web
// ports when they point at the same settings.json. The on-disk/JSON shape
// is therefore an array of 2-element [field-or-null, pattern] tuples
// (Python tuples serialise as JSON arrays), NOT a flat list of pattern
// strings — matching Qt's shape is what keeps the two ports interoperable.

/** [field, pattern] — field is null only for legacy Qt entries recorded
 *  before the per-field tagging existed (#347); a null field means "applies
 *  to any field" and such an entry is always visible in the Recent list. */
export type RecentPatternEntry = [string | null, string];

/** Settings key — identical string to Qt's select_dialog.py::_RECENT_KEY. */
export const RECENT_PATTERNS_KEY = "ui.action_dialog.recent_patterns" as const;

/** Cap on the number of stored entries — mirrors Qt's _RECENT_CAP. */
export const RECENT_PATTERNS_CAP = 10;

/**
 * Parse the raw settings value into a clean entry list, dropping anything
 * malformed (mirrors Qt's defensive parse in select_dialog.py's __init__:
 * any entry that isn't a 2-tuple of (str-or-null, non-empty str) is
 * dropped rather than crashing the dialog on a hand-edited or
 * cross-version-mismatched settings.json).
 */
export function parseRecentPatterns(raw: unknown): RecentPatternEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: RecentPatternEntry[] = [];
  for (const entry of raw) {
    if (
      Array.isArray(entry) &&
      entry.length === 2 &&
      (typeof entry[0] === "string" || entry[0] === null) &&
      typeof entry[1] === "string" &&
      entry[1] !== ""
    ) {
      out.push([entry[0], entry[1]]);
    }
  }
  // Cap on read too (#741): the writer caps at RECENT_PATTERNS_CAP, but a
  // hand-edited or cross-version settings.json could carry more — never render
  // an unbounded number of <option>s.
  return out.slice(0, RECENT_PATTERNS_CAP);
}

/**
 * Return a new entry list with (field, pattern) pushed to the front,
 * deduped by exact (field, pattern) match, and capped at
 * RECENT_PATTERNS_CAP. Mirrors Qt's _record_recent_pattern:
 *   - an empty (after trim) pattern is never recorded (#397 — Apply no
 *     longer gates on an empty pattern, so this guard is what keeps a
 *     no-op empty-pattern Apply out of the Recent list);
 *   - a pattern that fails to compile as a regex is never recorded (Recent
 *     should only carry patterns the user could productively re-pick).
 * Returns `existing` unchanged (same reference) when nothing should be
 * recorded, so callers can skip a settings write via a reference check.
 */
export function recordRecentPattern(
  existing: RecentPatternEntry[],
  field: string,
  pattern: string
): RecentPatternEntry[] {
  const trimmed = pattern.trim();
  if (trimmed === "") return existing;
  try {
    // Case-insensitive, mirroring Qt's re.compile(pattern, re.IGNORECASE)
    // validity gate (select_dialog.py). Case-sensitivity never affects JS
    // regex compilability, so this is a parity alignment, not a behaviour fix.
    new RegExp(trimmed, "i");
  } catch {
    return existing;
  }
  const entry: RecentPatternEntry = [field, trimmed];
  const deduped = existing.filter((e) => !(e[0] === entry[0] && e[1] === entry[1]));
  return [entry, ...deduped].slice(0, RECENT_PATTERNS_CAP);
}

/**
 * Entries visible in the Recent list for `currentField` — the current
 * field's own entries plus legacy null-field entries (which apply to any
 * field). Mirrors Qt's _show_recent_menu filter.
 */
export function visibleRecentPatterns(
  entries: RecentPatternEntry[],
  currentField: string
): RecentPatternEntry[] {
  return entries.filter(([field]) => field === null || field === currentField);
}
