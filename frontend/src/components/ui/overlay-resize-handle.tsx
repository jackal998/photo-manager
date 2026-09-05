// Bottom-right corner grab target for a movable/resizable overlay (#739).
//
// The visible half of the useOverlayGeometry mechanism: one 16px corner
// affordance, shared verbatim by the three overlay surfaces so the gesture
// looks and behaves identically on all of them. Purely presentational — the
// drag maths, clamping and persistence live in useOverlayGeometry.

import type { MouseEvent as ReactMouseEvent } from "react";
import { cn } from "@/lib/utils";

interface OverlayResizeHandleProps {
  "data-testid": string;
  onMouseDown: (e: ReactMouseEvent) => void;
  /** Extra classes — e.g. a light-on-dark variant for the full-res viewer. */
  className?: string;
  /** Accessible name; the surfaces pass a translated string. */
  label: string;
}

export function OverlayResizeHandle({
  "data-testid": testid,
  onMouseDown,
  className,
  label,
}: OverlayResizeHandleProps) {
  return (
    <div
      data-testid={testid}
      role="separator"
      aria-label={label}
      className={cn(
        "absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize select-none",
        // Two stacked corner rules, the conventional resize-gripper look.
        "after:absolute after:bottom-[3px] after:right-[3px] after:h-[7px] after:w-[7px]",
        "after:border-b-2 after:border-r-2 after:border-current after:opacity-50",
        "hover:after:opacity-100",
        className
      )}
      onMouseDown={onMouseDown}
    />
  );
}
