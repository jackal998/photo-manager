// Similarity-badge specs — the web port of the Daylight 5-state badge (#878).
//
// The UX baseline audit found the Similarity column overloading five distinct
// meanings — Ref / 100% / N% / N*% / "—" — as undifferentiated plain text,
// decodable only via a hover tooltip. Each state gets a pill whose colour,
// BORDER STYLE and FONT WEIGHT differ, plus (where the row text does not
// already carry one) a marker glyph.
//
// Grayscale / colour-blind safety is the load-bearing property here: the
// `(glyph, border, weight)` triple of every state is unique, so hue is never
// the only carrier of meaning. `similarityBadge.test.ts` asserts that.
//
// The row's accessible text is NOT changed by any of this — the badge wraps
// `similarityLabel()`'s output verbatim in its own span and the glyph, when
// one is prepended, sits in a separate `aria-hidden` element.

import type { Similarity } from "@/api/types";

/** The five visual states of the Similarity column, named after the design
 *  prototype (`docs/audits/web-port-feasibility-2026-06-19.md` §9.3). */
export type SimilarityBadgeState =
  | "ref"
  | "exact"
  | "near"
  | "indirect"
  | "none";

export interface SimilarityBadgeSpec {
  /** The marker character the badge displays. "" = this state has none. */
  glyph: string;
  /** True when the badge must render `glyph` itself, because the row text
   *  does not already begin with it (only `ref`: its label is the word
   *  "Ref"). False means the glyph arrives inside `similarityLabel()`. */
  prefixGlyph: boolean;
  /** Border style — the shape cue that survives a grayscale render. */
  border: "solid" | "dashed" | "dotted";
  /** Tailwind font-weight utility — the third, colour-free cue. */
  weight: string;
  /** Tailwind colour utilities (text / background / border), all three from
   *  `@theme` tokens in index.css. */
  colors: string;
}

/**
 * Per-state styling. Every `(glyph, border, weight)` triple below is unique
 * and every PAIR of states differs in at least one of those three — do not
 * "simplify" two states onto the same triple, that is the whole point.
 *
 *   ref      ★ Ref   gold, solid, semibold   the group's chosen keeper
 *   exact    100%    purple, solid, bold     byte-identical duplicate
 *   near     95% / ~ blue, solid, medium     direct near-match
 *   indirect ★ 92%   gray, DASHED, medium    transitive / indirect member
 *   none     —       muted, DOTTED, normal   no comparable image
 */
export const SIMILARITY_BADGE: Record<
  SimilarityBadgeState,
  SimilarityBadgeSpec
> = {
  ref: {
    glyph: "★",
    prefixGlyph: true,
    border: "solid",
    weight: "font-semibold",
    colors: "text-sim-ref-ink bg-sim-ref-bg border-sim-ref-line",
  },
  exact: {
    glyph: "",
    prefixGlyph: false,
    border: "solid",
    weight: "font-bold",
    colors: "text-sim-exact-ink bg-sim-exact-bg border-sim-exact-line",
  },
  near: {
    glyph: "",
    prefixGlyph: false,
    border: "solid",
    weight: "font-medium",
    colors: "text-sim-near-ink bg-sim-near-bg border-sim-near-line",
  },
  indirect: {
    // The glyph is already the first character of `similarityLabel()`'s
    // "★ 92%" (lib/format.ts) — the badge must not prepend a second one.
    glyph: "★",
    prefixGlyph: false,
    border: "dashed",
    weight: "font-medium",
    colors:
      "text-sim-indirect-ink bg-sim-indirect-bg border-sim-indirect-line",
  },
  none: {
    // Likewise: the label IS the em dash.
    glyph: "—",
    prefixGlyph: false,
    border: "dotted",
    weight: "font-normal",
    colors: "text-sim-none-ink bg-sim-none-bg border-sim-none-line",
  },
};

/** Border style → Tailwind utility. Written out as literals because Tailwind
 *  v4 scans source text for class names: an interpolated `border-${style}`
 *  compiles to nothing. */
const BORDER_CLASS: Record<SimilarityBadgeSpec["border"], string> = {
  solid: "border-solid",
  dashed: "border-dashed",
  dotted: "border-dotted",
};

export function similarityBadgeBorderClass(
  border: SimilarityBadgeSpec["border"]
): string {
  return BORDER_CLASS[border];
}

/**
 * Map the API's `similarity.kind` onto a badge state.
 *
 * The backend's five kinds (`core/app_service/review_view.py:71-100`) do not
 * line up one-to-one with the prototype's five visual states, because the
 * backend splits on "was a percentage computable" while the design splits on
 * "what KIND of duplicate relationship is this":
 *
 *   ref                       → ref        this row is the group's Ref winner
 *   percent, percent === 100  → exact      action EXACT — byte-identical
 *   percent, percent < 100    → near       REVIEW_DUPLICATE with a distance
 *   near_dup                  → near       REVIEW_DUPLICATE, distance unknown
 *   passenger                 → indirect   Ref-tier passenger (#536), starred
 *   none                      → none       no comparable pHash (Live Photo MOV)
 *
 * `near_dup` joins `near` because it is the same RELATIONSHIP — a direct
 * near-match — merely one whose percentage could not be recomputed; its label
 * stays the distinct "~" so the two remain tellable apart in the cell itself.
 */
export function similarityBadgeState(
  similarity: Similarity
): SimilarityBadgeState {
  switch (similarity.kind) {
    case "ref":
      return "ref";
    case "percent":
      return similarity.percent === 100 ? "exact" : "near";
    case "near_dup":
      return "near";
    case "passenger":
      return "indirect";
    case "none":
      return "none";
    default: {
      // Exhaustiveness guard, same shape as lib/format.ts: adding a
      // SimilarityKind without a case here is a compile error.
      const exhaustive: never = similarity.kind;
      throw new Error(`Unhandled similarity kind: ${String(exhaustive)}`);
    }
  }
}
