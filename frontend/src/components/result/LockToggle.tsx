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
import { DEFAULT_DENSITY, type Density } from "@/lib/density";
import { ROW_METRICS } from "@/lib/rowMetrics";

interface LockToggleProps {
  checked: boolean;
  onChange: (locked: boolean) => void;
  /** Row density (#878 layout slice R) — hit target 28/24px. */
  density?: Density;
  "data-testid"?: string;
}

export function LockToggle({
  checked,
  onChange,
  density = DEFAULT_DENSITY,
  "data-testid": testId,
}: LockToggleProps) {
  const t = useT();
  const metrics = ROW_METRICS[density];
  return (
    <button
      type="button"
      aria-pressed={checked}
      aria-label={t("web.column.lock", "Lock")}
      data-state={checked ? "checked" : "unchecked"}
      data-testid={testId}
      onClick={() => onChange(!checked)}
      className={cn(
        // 28×28, not 16×16 (REPLY §"Padlock (R17)"): «a 16px target is below a
        // comfortable pointer target and this is the control that protects a
        // file from bulk operations — it is the last thing that should be
        // fiddly. The glyph stays 14px; the growth is all hit area.»
        "inline-flex items-center justify-center transition-colors",
        metrics.lock,
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warm",
        // Faint vs solid IS the state cue; the open/closed shackle is the
        // second, colour-free one (the design's grayscale-safety rule).
        // `ink-hairline` is the ROLE name Q8 published for #a89f8f as a
        // non-text stroke — same value as the `ink-faint` alias this wore,
        // moved onto the role name index.css invites slice R to adopt.
        checked ? "text-warm" : "text-ink-hairline hover:text-ink-muted",
        "hover:bg-subtle"
      )}
    >
      {checked ? (
        <Lock className="h-[14px] w-[14px]" aria-hidden="true" />
      ) : (
        <LockOpen className="h-[14px] w-[14px]" aria-hidden="true" />
      )}
    </button>
  );
}
