// Unit tests for the Daylight 5-state similarity badge spec (#878).
//
// The bug these catch is the one the UX baseline audit filed in the first
// place: five distinct meanings collapsing into visuals a user cannot tell
// apart. A future edit that gives two states the same shape cues — or that
// routes a backend kind to the wrong state — restores exactly that defect,
// and no render test would notice because both states still "render".

import { describe, it, expect } from "vitest";

import type { Similarity, SimilarityKind } from "@/api/types";
import {
  SIMILARITY_BADGE,
  similarityBadgeState,
  similarityBadgeBorderClass,
  type SimilarityBadgeState,
} from "./similarityBadge";

const STATES: SimilarityBadgeState[] = [
  "ref",
  "exact",
  "near",
  "indirect",
  "none",
];

describe("similarity badge spec", () => {
  it("gives each of the five states a UNIQUE (glyph, border, weight) triple", () => {
    // Grayscale / colour-blind safety: hue must never be the only carrier of
    // the state. Two states sharing this triple are indistinguishable to a
    // user who cannot separate the hues.
    const triples = STATES.map((s) => {
      const spec = SIMILARITY_BADGE[s];
      return `${spec.glyph}|${spec.border}|${spec.weight}`;
    });
    expect(new Set(triples).size).toBe(STATES.length);
  });

  it("differs from every other state in at least one non-colour cue", () => {
    // Stronger than the set-size check above: it names the offending pair.
    for (const a of STATES) {
      for (const b of STATES) {
        if (a === b) continue;
        const x = SIMILARITY_BADGE[a];
        const y = SIMILARITY_BADGE[b];
        const differs =
          x.glyph !== y.glyph ||
          x.border !== y.border ||
          x.weight !== y.weight;
        expect(differs, `${a} and ${b} share every non-colour cue`).toBe(true);
      }
    }
  });

  it("maps each border style to a literal Tailwind class", () => {
    // An interpolated `border-${style}` compiles to nothing in Tailwind v4 —
    // the border cue would silently vanish from the built CSS.
    expect(similarityBadgeBorderClass("solid")).toBe("border-solid");
    expect(similarityBadgeBorderClass("dashed")).toBe("border-dashed");
    expect(similarityBadgeBorderClass("dotted")).toBe("border-dotted");
  });
});

describe("similarityBadgeState", () => {
  const cases: [Similarity, SimilarityBadgeState][] = [
    [{ kind: "ref", percent: null }, "ref"],
    [{ kind: "percent", percent: 100 }, "exact"],
    [{ kind: "percent", percent: 95 }, "near"],
    [{ kind: "near_dup", percent: null }, "near"],
    [{ kind: "passenger", percent: 92 }, "indirect"],
    [{ kind: "none", percent: null }, "none"],
  ];

  it.each(cases)("maps %o to its badge state", (similarity, expected) => {
    expect(similarityBadgeState(similarity)).toBe(expected);
  });

  it("covers every SimilarityKind the API can send", () => {
    // The API contract (api/types.ts, mirroring review_view.compute_similarity)
    // — a kind added there without a case here would throw at render time on a
    // real manifest.
    const kinds: SimilarityKind[] = [
      "percent",
      "ref",
      "passenger",
      "near_dup",
      "none",
    ];
    for (const kind of kinds) {
      expect(() =>
        similarityBadgeState({ kind, percent: 50 })
      ).not.toThrow();
    }
  });
});
