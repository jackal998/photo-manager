// multiSelect — pure click-to-selection math for a multi-select list.
//
// Encodes the plain / Ctrl(Cmd) / Shift semantics shared by every tree row
// selection in the app:
//   - plain click  → replace the selection with just the clicked row
//   - Ctrl/Cmd     → toggle the clicked row in/out of the selection
//   - Shift        → select the inclusive range between the anchor and the
//                    clicked row (over the currently-visible order)
//
// This MIRRORS the store's setSelection / toggleSelection / extendSelection
// (useAppStore.ts) which drive the MAIN result tree (#695). The execute dialog
// (#697) holds its OWN selection in local state — independent of the main tree
// — so it consumes this pure helper instead of the store actions. Kept pure
// (no React, no store) so the shift-range edge cases are unit-testable in
// isolation: no anchor yet, an anchor that scrolled out of the visible order,
// and a reverse (bottom-up) range.

export interface SelectionResult {
  /** Ordered list of selected paths (empty = nothing selected). */
  selectedPaths: string[];
  /** Shift-range anchor — the last plain/Ctrl click, or null. */
  anchorPath: string | null;
}

export function nextSelection(
  current: string[],
  anchor: string | null,
  clicked: string,
  mods: { ctrl: boolean; shift: boolean },
  orderedPaths: string[]
): SelectionResult {
  if (mods.shift) {
    const ai = anchor === null ? -1 : orderedPaths.indexOf(anchor);
    const ti = orderedPaths.indexOf(clicked);
    if (ai === -1 || ti === -1) {
      // No usable anchor (none yet, or it scrolled out of the visible order) →
      // behave like a plain click on the clicked row.
      return { selectedPaths: [clicked], anchorPath: clicked };
    }
    const [lo, hi] = ai <= ti ? [ai, ti] : [ti, ai];
    // Anchor stays put — successive Shift+clicks re-range from the origin.
    return { selectedPaths: orderedPaths.slice(lo, hi + 1), anchorPath: anchor };
  }
  if (mods.ctrl) {
    const next = current.includes(clicked)
      ? current.filter((p) => p !== clicked)
      : [...current, clicked];
    // Ctrl/Cmd+click re-anchors so a following Shift+click ranges from here.
    return { selectedPaths: next, anchorPath: clicked };
  }
  return { selectedPaths: [clicked], anchorPath: clicked };
}
