// 3-way decision control as a row of direct one-click buttons.
// States unchanged: "" (None) | "delete" | "ignore". Replaces the former
// Radix Select so staging a decision is one click ("直接按") instead of
// open-then-pick, matching the DesignSync prototype's decision affordance (#744).
// #878 slice (b) retones the three segments with the Daylight decision chips;
// the segments, their labels, their testids and the store action they dispatch
// are all unchanged — this is colour only.

import type { MouseEvent } from "react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/useT";
import type { DecisionValue } from "@/api/types";

interface DecisionControlProps {
  value: DecisionValue;
  onChange: (value: DecisionValue) => void;
  disabled?: boolean;
  "data-testid"?: string;
}

// slug is the testid suffix (empty DecisionValue "" → "none" so the id never
// ends in a bare dash and stays Playwright-addressable).
const OPTIONS: { value: DecisionValue; slug: string; label: string }[] = [
  { value: "", slug: "none", label: "None" },
  { value: "delete", slug: "delete", label: "Delete" },
  { value: "ignore", slug: "ignore", label: "Ignore" },
];

// Daylight decision chips (#878 slice b) — §9.3 `dec.*` of
// docs/audits/web-port-feasibility-2026-06-19.md, as @theme tokens.
//
// The ACTIVE segment wears its own decision's chip; every inactive segment
// wears `dec.undecided` (transparent fill, faint ink), so exactly one filled
// chip is visible per row and the staged decision reads at a glance — the UX
// baseline audit's headline finding was that it did not.
//
// `""` maps to dec.KEEP, not dec.undecided: under the #584 decision model
// `''` IS keep (the row survives Execute), and colouring the active `None`
// segment `undecided` would make active and inactive indistinguishable.
// `"ignore"` maps to dec.remove — "remove from the manifest" is what the
// prototype calls the `ignored` outcome.
const ACTIVE_CHIP: Record<DecisionValue, string> = {
  "": "bg-dec-keep-bg text-dec-keep-ink",
  delete: "bg-dec-delete-bg text-dec-delete-ink",
  ignore: "bg-dec-remove-bg text-dec-remove-ink",
};

const INACTIVE_CHIP =
  "bg-dec-undecided-bg text-dec-undecided-ink hover:bg-subtle hover:text-ink";

export function DecisionControl({
  value,
  onChange,
  disabled = false,
  "data-testid": testId,
}: DecisionControlProps) {
  const t = useT();

  function handleClick(e: MouseEvent, v: DecisionValue) {
    // Staging a decision shouldn't also select the row — stop the click from
    // bubbling to FileRow's row-select handler.
    e.stopPropagation();
    onChange(v);
  }

  return (
    <div
      role="group"
      // Copy audit R5 — the group's accessible name was hardcoded English.
      // The three OPTION LABELS below are deliberately untouched: the
      // decision vocabulary (None/Delete/Ignore vs Keep/Delete/Remove) is
      // audit finding R3/R4, still awaiting the owner's decision.
      aria-label={t("web.column.decision", "Decision")}
      data-testid={testId}
      className="inline-flex rounded border border-dec-undecided-line overflow-hidden"
    >
      {OPTIONS.map((opt, i) => {
        const active = value === opt.value;
        return (
          <button
            key={opt.slug}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            data-testid={testId ? `${testId}-${opt.slug}` : undefined}
            onClick={(e) => handleClick(e, opt.value)}
            className={cn(
              // Geometry is unchanged from the neutral version on purpose:
              // same h-7/px-2/text-xs and the same 1px dividers, so the cell's
              // width does not move and no scenario that locates a row by
              // testid shifts. Only the colours are retoned.
              "h-7 px-2 text-xs transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-warm",
              i > 0 && "border-l border-dec-undecided-line",
              active ? ACTIVE_CHIP[opt.value] : INACTIVE_CHIP,
              disabled && "opacity-50 cursor-not-allowed"
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
