// Where a cursor-anchored `position: fixed` menu goes so it stays on screen
// (#897). Pure — the component measures, this decides.
//
// Qt's QMenu, which the desktop menu was, never let an item leave the screen:
// it opens below-right of the cursor, flips to the other side of the cursor on
// the axis that would overflow, slides along an axis where neither side fits,
// and scrolls when it is taller than the screen itself. The web menu used to
// open at the cursor unconditionally, so in a 400 px window its lower items
// sat below the viewport edge and could not be clicked at all.

/** Gap kept between the menu and the viewport edge, in CSS px. */
export const MENU_VIEWPORT_MARGIN = 4;

export interface Size {
  width: number;
  height: number;
}

export interface MenuPlacement {
  left: number;
  top: number;
  /** Set only when the menu is taller than the viewport: it scrolls inside
   *  this height instead of running off the bottom. */
  maxHeight: number | null;
}

/**
 * One axis: open forward from the cursor, else flip back across it, else
 * slide in so the far edge meets the viewport edge. Never before `margin`.
 */
function placeOnAxis(
  cursor: number,
  size: number,
  viewport: number,
  margin: number
): number {
  if (cursor + size <= viewport - margin) return Math.max(margin, cursor);
  if (cursor - size >= margin) return cursor - size;
  return Math.max(margin, viewport - margin - size);
}

export function placeMenu(
  cursor: { x: number; y: number },
  menu: Size,
  viewport: Size,
  margin: number = MENU_VIEWPORT_MARGIN
): MenuPlacement {
  const usableHeight = viewport.height - 2 * margin;
  const scrolls = menu.height > usableHeight;
  return {
    left: placeOnAxis(cursor.x, menu.width, viewport.width, margin),
    top: scrolls ? margin : placeOnAxis(cursor.y, menu.height, viewport.height, margin),
    maxHeight: scrolls ? Math.max(0, usableHeight) : null,
  };
}
