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
