// SimilarityLegend — the five similarity states, named, pinned to the bottom
// of the preview pane (layout slice E; audit row P8).
//
// The badge is a five-way code (★ Ref / 100% / 96% / ★ 95% / —) whose meaning
// lived only in a hover tooltip on the cell. A legend that scrolls away with
// the metadata is a legend nobody finds at the moment they need it, so this
// sits OUTSIDE the pane's scroller (PreviewPane mounts it as the flex row's
// last, non-shrinking child) and is visible whether or not a file is selected.
//
// Every row is built from the SAME two functions the row cell and the pane's
// own badge call — `similarityBadgeState()` for the (colour, border, weight)
// triple and `similarityLabel()` for the text. Hardcoding five pills here
// would let the legend keep describing a vocabulary the table no longer uses;
// deriving them means a change to either function shows up in the legend on
// the next render.

import { useT } from "@/i18n/useT";
import { cn } from "@/lib/utils";
import { similarityLabel } from "@/lib/format";
import {
  SIMILARITY_BADGE,
  similarityBadgeBorderClass,
  similarityBadgeState,
} from "@/lib/similarityBadge";
import type { Similarity } from "@/api/types";
import { PREVIEW_LEGEND } from "@/testids";

/**
 * One representative `Similarity` per visual state, plus the phrase that says
 * what the state MEANS. The percentages are examples (96 / 95), exactly as the
 * prototype's legend prints them — the legend explains the shape of the cell,
 * not any particular row's number.
 */
const LEGEND_ROWS: {
  similarity: Similarity;
  key: string;
  fallback: string;
}[] = [
  {
    similarity: { kind: "ref", percent: null },
    key: "web.preview.legend_ref",
    fallback: "chosen keeper",
  },
  {
    similarity: { kind: "percent", percent: 100 },
    key: "web.preview.legend_exact",
    fallback: "exact duplicate",
  },
  {
    similarity: { kind: "percent", percent: 96 },
    key: "web.preview.legend_near",
    fallback: "near match",
  },
  {
    similarity: { kind: "passenger", percent: 95 },
    key: "web.preview.legend_indirect",
    fallback: "linked indirectly",
  },
  {
    similarity: { kind: "none", percent: null },
    key: "web.preview.legend_none",
    fallback: "video / no image",
  },
];

export function SimilarityLegend() {
  const t = useT();
  return (
    <div
      data-testid={PREVIEW_LEGEND}
      className="flex-shrink-0 border-t border-hairline-soft px-4 pb-4 pt-[15px]"
    >
      <div className="mb-[9px] text-[10px] font-bold uppercase tracking-[.05em] text-ink-muted select-none">
        {t("web.preview.legend_title", "Similarity legend")}
      </div>
      <div className="flex flex-col gap-[7px] text-[11.5px] text-ink-muted">
        {LEGEND_ROWS.map((row) => {
          const state = similarityBadgeState(row.similarity);
          const badge = SIMILARITY_BADGE[state];
          return (
            <div key={state} className="flex items-center gap-[9px]">
              {/* The one place the badge takes a min-width: five pills in a
                  column read as a key only when their left and right edges
                  line up. In a ROW the badge still sizes to its content. */}
              <span
                data-legend-state={state}
                className={cn(
                  "inline-flex min-w-[74px] flex-shrink-0 items-center justify-center gap-[5px] rounded-[5px] border px-2 py-[2px] text-[11px]",
                  similarityBadgeBorderClass(badge.border),
                  badge.weight,
                  badge.colors
                )}
              >
                {badge.prefixGlyph && (
                  <span aria-hidden="true" className="text-[12px] leading-none">
                    {badge.glyph}
                  </span>
                )}
                <span>{similarityLabel(row.similarity, t)}</span>
              </span>
              <span>{t(row.key, row.fallback)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
