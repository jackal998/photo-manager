// Measure a cursor-anchored menu and keep it inside the window (#897).
//
// The deciding math is lib/menuPlacement.placeMenu; this hook is only the
// measuring half, shared by the two right-click menus:
//   - ContextMenu (result tree) is `position: fixed`, so its {x, y} are window
//     coordinates.
//   - ExecuteContextMenu is `position: absolute` inside a layer over the
//     Execute dialog (mounted there to dodge the Radix modal's pointer
//     traps), so its {x, y} are relative to that layer — the dialog sits
//     anywhere on screen, and the window is still the edge that matters
//     (the dialog deliberately does not clip the menu, s30).
// Both are handled by working in window coordinates and shifting by the
// menu's containing block: `offsetParent` is the layer for the absolute menu
// and null for the fixed one.
//
// Measured in a layout effect so the menu is re-placed before the browser
// paints it. scrollHeight is the full content height even after a maxHeight
// has clipped the box; offsetHeight - clientHeight adds the border back.

import {
  useLayoutEffect,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import { placeMenu, type MenuPlacement } from "@/lib/menuPlacement";

export function useMenuPlacement(
  menuRef: RefObject<HTMLDivElement | null>,
  x: number,
  y: number
): CSSProperties {
  const [placement, setPlacement] = useState<MenuPlacement | null>(null);

  useLayoutEffect(() => {
    const el = menuRef.current;
    if (el === null) return;
    const origin = el.offsetParent?.getBoundingClientRect() ?? {
      left: 0,
      top: 0,
    };
    const p = placeMenu(
      { x: origin.left + x, y: origin.top + y },
      {
        width: el.offsetWidth,
        height: el.scrollHeight + (el.offsetHeight - el.clientHeight),
      },
      { width: window.innerWidth, height: window.innerHeight }
    );
    setPlacement({ ...p, left: p.left - origin.left, top: p.top - origin.top });
  }, [menuRef, x, y]);

  return {
    left: placement?.left ?? x,
    top: placement?.top ?? y,
    ...(placement?.maxHeight != null
      ? { maxHeight: placement.maxHeight, overflowY: "auto" }
      : {}),
  };
}
