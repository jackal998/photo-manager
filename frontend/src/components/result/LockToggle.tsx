// Lock toggle — a clickable padlock (#878 slice b).
//
// Was a Radix Checkbox. The Daylight design draws the lock as a padlock that
// is FAINT when the row is unlocked and SOLID (warm accent) when it is locked,
// so the locked rows in a long tree are scannable without reading each cell —
// a checkbox renders every row's control at the same weight.
//
// Two contracts are deliberately preserved so nothing downstream changes:
//   * `data-state="checked" | "unchecked"` — what Radix emitted and what the
//     existing ResultTree tests and any scenario built on them read.
//   * the click bubbles to FileRow's row-select handler exactly as the Radix
//     root's did; clicking a padlock still also selects its row.
// `aria-pressed` is added (a toggle button's native state). The accessible
// name now comes from web.column.lock — the same word as the column header
// the control sits under — instead of a hardcoded English "Lock row" that a
// zh_TW screen-reader user would still have heard in English (copy audit R9).

import { Lock, LockOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/useT";

interface LockToggleProps {
  checked: boolean;
  onChange: (locked: boolean) => void;
  "data-testid"?: string;
}

export function LockToggle({
  checked,
  onChange,
  "data-testid": testId,
}: LockToggleProps) {
  const t = useT();
  return (
    <button
      type="button"
      aria-pressed={checked}
      aria-label={t("web.column.lock", "Lock")}
      data-state={checked ? "checked" : "unchecked"}
      data-testid={testId}
      onClick={() => onChange(!checked)}
      className={cn(
        "inline-flex h-4 w-4 items-center justify-center rounded-sm transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warm",
        // Faint vs solid IS the state cue; the open/closed shackle is the
        // second, colour-free one (the design's grayscale-safety rule).
        checked ? "text-warm" : "text-ink-faint hover:text-ink-muted"
      )}
    >
      {checked ? (
        <Lock className="h-3.5 w-3.5" aria-hidden="true" />
      ) : (
        <LockOpen className="h-3.5 w-3.5" aria-hidden="true" />
      )}
    </button>
  );
}
