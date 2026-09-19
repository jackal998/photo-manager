// Toolbar shedding (#878, layout slice TB — round-2 review).
//
// The strip is `flex-nowrap` at a fixed 52px, so it has to FIT rather than
// wrap. Shrinking the two text inputs buys enough room at 1280px, but below
// that the compression runs out and the strip starts to scroll — and the
// control at the scrolled-off end is the Delete CTA, the only destructive
// thing on the screen. A toolbar you have to scroll to reach a delete button
// is worse than one that drops a control.
//
// So one control group SHEDS, chosen because it is the only one with an exact
// duplicate elsewhere: the web-only manifest path input and its Open button
// both do what **File → Open Manifest…** does (which opens the filesystem
// picker — strictly the better affordance of the two). Nothing becomes
// unreachable; one redundant path does.
//
// The same shape as slice C's column shedding: a PURE function of the measured
// width, so the threshold is table-testable without a DOM and one decision
// feeds every consumer. `overflow-x: auto` stays on the strip underneath this
// as the last-resort net for a width or a font we did not anticipate.

/** What the toolbar renders at a given width. */
export interface ToolbarShedPlan {
  /** Render the manifest path input + its Open button. */
  manifestOpen: boolean;
}

/**
 * Below this measured toolbar width the manifest-open pair sheds.
 *
 * 1100 is not a round number chosen for looks: at 1280 the strip measured
 * exactly 1280 with the inputs at their floors under a wide (non-Segoe) stack,
 * and the manifest pair is ~180px of that. Dropping it at 1100 keeps the
 * remaining controls above their own floors down to roughly 920px, which is
 * where slice C's own column budget has already started shedding.
 */
export const TOOLBAR_MANIFEST_SHED_WIDTH = 1100;

/**
 * Decide what the toolbar shows at `width` px.
 *
 * `null` means "not measured yet" (first paint, or a headless environment
 * where every layout API returns zero) and renders EVERYTHING — a missing
 * measurement must never masquerade as a narrow toolbar and silently remove a
 * control, which is the failure mode slice C's `visibleColumns` guards the
 * same way.
 */
export function toolbarShedPlan(width: number | null): ToolbarShedPlan {
  if (width === null || !Number.isFinite(width) || width <= 0) {
    return { manifestOpen: true };
  }
  return { manifestOpen: width >= TOOLBAR_MANIFEST_SHED_WIDTH };
}
