// Web analog of Qt's COL_TO_FIELD (app/views/handlers/dialog_handler_helpers.py
// :63-72) — maps a right-clicked result-tree column to the ActionDialog
// field-option value it should pre-fill (#735).
//
// Only columns that have BOTH a Qt COL_TO_FIELD semantic AND a matching valid
// web ActionDialog field option are mapped. "score" and "dims" have no Qt
// COL_TO_FIELD entry, so a right-click on those columns opens the dialog with
// no pre-fill (same as today).
//
// ACTION_DIALOG_FIELD_OPTIONS duplicates the `label` values from
// ActionDialog.tsx's FIELDS array (the <select> option values) rather than
// importing them — #735's diff is scoped to not touch ActionDialog.tsx. Keep
// this list byte-identical to that array if it ever changes.

import type { FileRow } from "@/api/types";
import { escapeRegex } from "@/lib/regexEscape";
import type { ColumnId } from "@/lib/resultColumns";

/** Column key -> ActionDialog field-option value (see ActionDialog.tsx FIELDS). */
export const WEB_COL_TO_FIELD: Partial<Record<ColumnId, string>> = {
  name: "File Name",
  similarity: "Similarity",
  action: "Action",
  size: "Size (Bytes)",
  date: "Shot Date",
};

/** All valid ActionDialog field-option values. Used to validate an
 *  `openActionDialog(initialField)` caller-supplied value before writing it
 *  into store state — an unrecognised value must not corrupt the dialog's
 *  <select>, which only ever renders these 11 options. */
export const ACTION_DIALOG_FIELD_OPTIONS: ReadonlySet<string> = new Set([
  "File Name",
  "Folder",
  "Size (Bytes)",
  "Group Count",
  "Similarity",
  "Score",
  "Creation Date",
  "Shot Date",
  "Resolution",
  "Action",
  "Lock",
]);

/**
 * Resolve the ActionDialog initial-field value for a right-clicked column.
 * Mirrors Qt's `resolve_initial_field` (dialog_handler_helpers.py:104-115).
 * Returns undefined for an unmapped/absent column — the caller then opens
 * the dialog argless (no pre-fill), same as clicking "Set Action by Field…"
 * with no column context (e.g. a group-row right-click).
 */
export function resolveInitialField(col: string | undefined): string | undefined {
  if (col === undefined) return undefined;
  return WEB_COL_TO_FIELD[col as ColumnId];
}

// ---------------------------------------------------------------------------
// #893 — highlighted-row value seed (the VALUE half of #735's field pre-fill)
// ---------------------------------------------------------------------------

/**
 * Field -> the highlighted row's value for that field. Web analog of the dict
 * Qt's `_get_highlighted_row_values` handed to `ActionDialog(row_values=…)`
 * (app/views/handlers/dialog_handler.py:119-160).
 */
export type RowValues = Readonly<Record<string, string>>;

/**
 * Build the seed dict from the highlighted row.
 *
 * Qt collected CHILD_ROW_FIELDS (Action / File Name / Folder / Size (Bytes) /
 * Creation Date / Shot Date) plus the group-level Similarity / Group Count.
 * Only the three NON-numeric ones are seedable on the web: the other five are
 * in `ActionDialog.NUMERIC_FIELDS` and render NumericPanel, whose pattern is a
 * `__cmp__:` / `__top_n__:` pseudo-pattern that an escaped-literal seed would
 * corrupt. (Qt had the same split — it stamped the regex line-edit, which is
 * hidden in numeric mode — so nothing is lost.)
 *
 * The three values are exactly what the server matches a regex against, so the
 * seed always matches the row it came from: `basename` and `folder` are
 * `Path(file_path).name` / `record.folder_path` (core/app_service/
 * review_view.py:134-135) and those are what `_get_record_field` returns for
 * "File Name" / "Folder"; "Action" maps to `user_decision` on both sides
 * (core/app_service/action_resolve.py:43-46).
 */
export function rowValuesForSeed(row: FileRow | null | undefined): RowValues {
  if (row === null || row === undefined) return {};
  return {
    "File Name": row.basename ?? "",
    Folder: row.folder ?? "",
    Action: row.user_decision ?? "",
  };
}

/**
 * The auto-seeded pattern for `field`, or "" when the row has no usable value.
 *
 * "contains" (a bare escaped literal, no ^/$ anchors) mirrors Qt's
 * `_apply_exact_regex_for_current_field` after B10/#348 — RegexPanel's Simple
 * row reverse-parses it back to ("contains", value), the documented default op.
 */
export function seedPatternFor(rowValues: RowValues, field: string): string {
  const value = rowValues[field];
  if (typeof value !== "string" || value === "") return "";
  return escapeRegex(value);
}
