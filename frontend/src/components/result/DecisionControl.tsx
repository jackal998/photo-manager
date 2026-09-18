// 3-way decision control as a row of direct one-click buttons.
// States unchanged: "" (None) | "delete" | "ignore". Replaces the former
// Radix Select so staging a decision is one click ("直接按") instead of
// open-then-pick, matching the DesignSync prototype's decision affordance (#744).
// #878 slice (b) retones the three segments with the Daylight decision chips;
// layout slice R then gives them the REPLY's inset TRACK and its two density
// value sets. Throughout both: the segments, their labels, their testids, the
// `aria-pressed` contract and the store action they dispatch are unchanged —
// only colour and geometry move.

import type { MouseEvent } from "react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/useT";
import type { DecisionValue } from "@/api/types";
import { DEFAULT_DENSITY, type Density } from "@/lib/density";
import { ROW_METRICS } from "@/lib/rowMetrics";

interface DecisionControlProps {
  value: DecisionValue;
  onChange: (value: DecisionValue) => void;
  disabled?: boolean;
  /** Row density (#878 layout slice R) — track 32/28px, segments 26/22px. */
  density?: Density;
  "data-testid"?: string;
}

// slug is the testid suffix (empty DecisionValue "" → "none" so the id never
// ends in a bare dash and stays Playwright-addressable) — UNCHANGED by the
// vocabulary switch, so no scenario locator moves.
//
// `labelKey`/`titleKey` are the two halves of the ONE decision vocabulary
// (copy audit R3/R4, owner decision 2026-09-18): the short word on the
// segment, the long form in its tooltip. `remove_from_list` is still the
// catalog key for the `ignore` wire value — only the rendered words are new.
const OPTIONS: {
  value: DecisionValue;
  slug: string;
  labelKey: string;
  label: string;
  title: string;
}[] = [
  { value: "", slug: "none", labelKey: "keep", label: "Keep", title: "Keep this file" },
  {
    value: "delete",
    slug: "delete",
    labelKey: "delete",
    label: "Delete",
    title: "Delete — move to Recycle Bin",
  },
  {
    value: "ignore",
    slug: "ignore",
    labelKey: "remove_from_list",
    label: "Skip",
    title: "Skip — leave on disk, drop from this review",
  },
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
// `''` IS keep (the row survives Execute), and colouring the active `Keep`
// segment `undecided` would make active and inactive indistinguishable.
// `"ignore"` maps to dec.remove — the TOKEN name is the prototype's word for
// the `ignored` outcome; the segment itself reads "Skip".
//
// Layout slice R adds the REPLY's per-segment BORDER and WEIGHT to the fills:
// Keep and Skip carry a 1px line in their own hue at 600, Delete carries
// «no border» and white 700 — «Delete stays the only solid dark fill with a
// light label in the control», the same grayscale-inversion rule the badges
// follow. Every segment reserves the 1px as `border-transparent` when it is not
// selected, so selection can never shift the control by a pixel.
const ACTIVE_CHIP: Record<DecisionValue, string> = {
  "": "bg-dec-keep-bg border-dec-keep-line text-dec-keep-ink font-semibold",
  delete: "bg-dec-delete-bg border-transparent text-dec-delete-ink font-bold",
  ignore: "bg-dec-remove-bg border-dec-remove-line text-dec-remove-ink font-semibold",
};

// Unselected: no fill, no line, `ink-muted` at 500 (REPLY's table). Hover is
// «#ffffff at 60% over the track» with the text promoted to full ink — a wash
// ON the track rather than a second surface colour, so it cannot be mistaken
// for a selection. The selected segment gets NO hover change.
const INACTIVE_CHIP =
  "bg-dec-undecided-bg border-transparent text-ink-muted font-medium hover:bg-white/60 hover:text-ink";

export function DecisionControl({
  value,
  onChange,
  disabled = false,
  density = DEFAULT_DENSITY,
  "data-testid": testId,
}: DecisionControlProps) {
  const t = useT();
  const metrics = ROW_METRICS[density];

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
      aria-label={t("web.column.decision", "Decision")}
      data-testid={testId}
      data-decision-track=""
      className={cn(
        // An INSET TRACK, not a joined box (REPLY §"Decision control (R16)"):
        // «the track is what makes three mutually exclusive options read as one
        // control with a current value rather than three adjacent buttons — and
        // it gives the selected segment a surface to sit ON». Content-sized on
        // purpose: «~164px en · ~148px zh-TW (content-sized; do not fix)».
        "inline-flex items-center border border-hairline bg-dec-track",
        metrics.decisionTrack
      )}
    >
      {OPTIONS.map((opt) => {
        const active = value === opt.value;
        return (
          <button
            key={opt.slug}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            data-testid={testId ? `${testId}-${opt.slug}` : undefined}
            title={t(`web.decision_long.${opt.labelKey}`, opt.title)}
            onClick={(e) => handleClick(e, opt.value)}
            className={cn(
              // Segment geometry is density-keyed (26/22px tall, 0 10px /
              // 0 8px padding, radius 7px); the 1px border is always present —
              // transparent when unselected — so selecting never reflows.
              "inline-flex items-center border text-[12px] leading-none transition-colors",
              metrics.decisionSegment,
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-warm",
              active ? ACTIVE_CHIP[opt.value] : INACTIVE_CHIP,
              disabled && "opacity-50 cursor-not-allowed"
            )}
          >
            {t(`web.decision.${opt.labelKey}`, opt.label)}
          </button>
        );
      })}
    </div>
  );
}
