// Row-move animation for an explicit decision re-sort (#923, design F3):
// «re-sort: click the header (or the menu entry) — rows animate to their new
// position over 120ms ease-out». Silent re-sorts (collapse, filter, density)
// and the first application of a sort do NOT animate; the store only bumps
// `decisionResortSeq` for the explicit one.
//
// A FLIP over the virtualised rows. The tree's rows are INDEX-keyed, so after
// a re-sort the wrapper at index i shows a different file. Each wrapper is
// offset by (the file's previous start − its new start) and then transitioned
// back to zero, which draws every file sliding from where it was to where it
// now is. The offset is written to the CSS `translate` property, NOT to
// `transform`: React owns each wrapper's `transform: translateY(start)`, and
// the two properties compose, so the animation never fights the virtualiser.
//
// The previous starts come from the virtualiser's own numbers (no DOM read per
// commit): a map of the MOUNTED rows' file paths to their `start`, refreshed
// after every commit. A file that was not mounted before the re-sort has no
// previous start and simply appears in place.

import { useLayoutEffect, useRef, type RefObject } from "react";

/** F3's timing. Exported so the unit test asserts the ruled values. */
export const RESORT_TRANSITION = "translate 120ms ease-out";
const CLEANUP_MS = 160;

interface MountedRow {
  index: number;
  start: number;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

function slide(el: HTMLElement, fromPx: number): void {
  el.style.transition = "none";
  el.style.translate = `0 ${fromPx}px`;
  // Force the offset frame to be laid out before the transition starts —
  // otherwise the browser coalesces both writes and nothing animates.
  void el.offsetHeight;
  el.style.transition = RESORT_TRANSITION;
  el.style.translate = "0 0";
  window.setTimeout(() => {
    el.style.transition = "";
    el.style.translate = "";
  }, CLEANUP_MS);
}

export function useResortAnimation(
  containerRef: RefObject<HTMLElement | null>,
  rows: readonly MountedRow[],
  pathAt: (index: number) => string | undefined,
  resortSeq: number
): void {
  const previousStarts = useRef<Map<string, number>>(new Map());
  const seenSeq = useRef(resortSeq);

  // No dependency list on purpose: the starts must be refreshed after EVERY
  // commit so they are current when a re-sort lands. The work is bounded by
  // the mounted rows (the virtualiser's window), not by the manifest.
  useLayoutEffect(() => {
    if (seenSeq.current !== resortSeq) {
      seenSeq.current = resortSeq;
      const container = containerRef.current;
      if (container !== null && !prefersReducedMotion()) {
        for (const row of rows) {
          const path = pathAt(row.index);
          const from = path === undefined ? undefined : previousStarts.current.get(path);
          if (from === undefined || from === row.start) continue;
          const el = container.querySelector<HTMLElement>(`[data-index="${row.index}"]`);
          if (el !== null) slide(el, from - row.start);
        }
      }
    }
    const next = new Map<string, number>();
    for (const row of rows) {
      const path = pathAt(row.index);
      if (path !== undefined) next.set(path, row.start);
    }
    previousStarts.current = next;
  });
}
