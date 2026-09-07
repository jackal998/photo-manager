// 3-way decision control as a row of direct one-click buttons.
// States unchanged: "" (None) | "delete" | "ignore". Replaces the former
// Radix Select so staging a decision is one click ("直接按") instead of
// open-then-pick, matching the DesignSync prototype's decision affordance (#744).

import type { MouseEvent } from "react";
import { cn } from "@/lib/utils";
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

export function DecisionControl({
  value,
  onChange,
  disabled = false,
  "data-testid": testId,
}: DecisionControlProps) {
  function handleClick(e: MouseEvent, v: DecisionValue) {
    // Staging a decision shouldn't also select the row — stop the click from
    // bubbling to FileRow's row-select handler.
    e.stopPropagation();
    onChange(v);
  }

  return (
    <div
      role="group"
      aria-label="Decision"
      data-testid={testId}
      className="inline-flex rounded border border-neutral-200 overflow-hidden"
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
              "h-7 px-2 text-xs transition-colors",
              i > 0 && "border-l border-neutral-200",
              active
                ? "bg-neutral-900 text-white"
                : "bg-white text-neutral-700 hover:bg-neutral-100",
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
